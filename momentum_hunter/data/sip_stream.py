"""Dueño único del websocket de barras SIP.

POR QUÉ. El REST de `data.alpaca.markets` llega ~1 minuto tarde y el
vigía lo vuelve a pedir en cada tick. Este proceso abre UNA conexión a
`wss://stream.data.alpaca.markets/v2/sip`, se suscribe a las barras de
minuto de los símbolos que ya están en la watchlist o en una posición
abierta, y deja las velas en disco. No es un bróker: no hay endpoint de
órdenes ni de posiciones. Las posiciones se leen de `revisiones.json`,
que el ejecutor ya escribió.

MODO SOMBRA. `MOMENTUM_SIP_STREAM` vale `sombra` (default), `primario`
o `0`. En sombra —y con `0`— nadie decide con este almacén. `primario`
está implementado en `barras_si_cubren` y NO se enciende desde la
unidad ni desde el wrapper: si el almacén está viejo, desconectado o
incompleto, el pedido de velas sigue por el REST de siempre. Un campo
ausente no se convierte en cero.

TRES SESIONES. La sombra no se apaga sola al tercer día. Pasar a
`primario` es una decisión del dueño, en el entorno del vigía y en la
unidad, los dos a la vez. Este módulo no cuenta sesiones para
promocionarse.

LÍMITES. Algo Trader Plus (el plan que da SIP) permite símbolos
ilimitados en el websocket de acciones y, como casi todos los planes,
UNA conexión a este endpoint. Un segundo socket recibe 406 y no
reemplaza al primero. No se manda el wildcard `*`. El tope local es un
cinturón por si un bug mete el universo entero: primero posiciones,
después TRIGGERED, después WATCHING.

TRADES Y QUOTES. No hacen falta para armar la vela: el canal `bars` ya
es el minuto agregado, y `updatedBars` es la misma vela corregida
cuando entra un trade tarde. Suscribirse a trades para reconstruir el
minuto sería otra cinta y otro límite de mensajes.

El almacén vive en `MOMENTUM_ESTADO_DIR/sip_stream/` (la variable, si
no está, es `/var/lib/momentum/estado`, la misma raíz que
`rutas_estado`). Watchlist y revisiones se leen de ahí, no del
checkout. Nada de esto se escribe dentro del árbol git.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import logging
import os
import signal
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from momentum_hunter.data.alpaca_datos import _listas, _simbolo_para_query
from momentum_hunter.models import BarraIntradia
from momentum_hunter.rutas_estado import DEFAULT_RAIZ as ESTADO_DEFAULT

log = logging.getLogger("momentum_hunter.data.sip_stream")

# Fijo. No sale de una variable: un error de config no puede apuntar
# este proceso a otro host (sandbox, IEX, o el stream de la cuenta).
URL_STREAM_SIP = "wss://stream.data.alpaca.markets/v2/sip"
HOST_DATOS = "stream.data.alpaca.markets"
PATH_SIP = "/v2/sip"

ENV_MODO = "MOMENTUM_SIP_STREAM"
ENV_ESTADO = "MOMENTUM_ESTADO_DIR"

# Basic corta en 30. Algo Trader Plus no publica tope de símbolos en
# acciones. 200 cubre watchlist + posiciones con margen y frena un
# universo colado. No es un umbral de trading.
SIMBOLOS_TOPE = 200
CONEXIONES_MAX = 1

VERSION_ESTADO = 1
MAX_FALLOS_AUTH = 3
ESPERA_AUTH_S = 10.0
HEARTBEAT_S = 60.0
# El silencio de la cinta no abre hueco: Alpaca no manda la vela de un
# minuto sin trades, y un símbolo quieto no es un socket muerto. El
# hueco es la caída. El heartbeat igual anota el silencio, y el
# comparador contra REST ve el minuto que sí existió y acá no está.
BACKOFF_BASE_S = 1.0
BACKOFF_TOPE_S = 30.0
# Un latido más viejo que esto, con el reloj del lector, es un almacén
# que ya no está siendo escrito. No se opera con esa foto.
FRESCURA_MAX_S = 180.0
COMPARAR_CADA_S = 300.0
_LARGO_MINIMO_SECRETO = 12
_NY = ZoneInfo("America/New_York")
_ESTADOS_INTERES = frozenset({"watching", "triggered"})
# Cuánto hueco guardamos. Por debajo de la ventana de 5 días del hunter
# no se puede afirmar que el intervalo estuvo limpio.
_RETENCION_HUECOS = timedelta(days=8)

_CODIGOS_AUTH = frozenset({401, 402, 403, 404})


class UrlNoEsSip(Exception):
    """El arranque se niega. El mensaje es fijo: la url puede traer
    una clave y no se repite."""


class ConexionCerrada(Exception):
    pass


class ParadaPedida(Exception):
    pass


class EstadoIlegible(Exception):
    """`estado.json` existe y no se puede leer. No se reescribe: pisarlo
    con vacío haría desaparecer el último latido."""


class YaCorre(Exception):
    """Otro proceso tiene el candado. Dos sockets a SIP se pisan el
    límite de una conexión."""


class EstadoDentroDelRepo(Exception):
    """El almacén iba a caer dentro del checkout. No se crea."""


class WildcardProhibido(Exception):
    """El server confirmó `*`. No lo pedimos y no es "todos los
    símbolos de la watchlist"."""


def afirmar_url_sip(url: str) -> str:
    """Devuelve la url SIP canónica o lanza `UrlNoEsSip`.

    Tiene que ser exactamente `URL_STREAM_SIP`: `wss` contra
    `stream.data.alpaca.markets` con path `/v2/sip`, sin usuario, clave,
    query ni fragmento. Cambiar la constante hacia otro host también
    falla."""
    if not isinstance(url, str):
        raise UrlNoEsSip("se rechazó el stream: no es el SIP de datos")
    partes = urlsplit(url)
    canonico = (
        url == URL_STREAM_SIP
        and partes.scheme == "wss"
        and partes.hostname == HOST_DATOS
        and partes.path == PATH_SIP
        and partes.username is None
        and partes.password is None
        and partes.query == ""
        and partes.fragment == ""
    )
    if not canonico:
        raise UrlNoEsSip("se rechazó el stream: no es el SIP de datos")
    return URL_STREAM_SIP


def modo_configurado(valor: str | None = None) -> str:
    """`sombra` (default), `primario` o `0`.

    Un texto desconocido NO enciende `primario`: un typo tiene que
    dejar la decisión en el REST, que es lo que ya corre."""
    if valor is None:
        valor = os.environ.get(ENV_MODO, "sombra")
    s = valor.strip().lower() if isinstance(valor, str) else ""
    if s in ("primario", "primary"):
        return "primario"
    if s in ("0", "off", "false", "no", "apagado"):
        return "0"
    return "sombra"


def espera_de_reintento(intento: int, *, base: float = BACKOFF_BASE_S, tope: float = BACKOFF_TOPE_S) -> float:
    """Segundos antes de volver a conectar. `intento` 1 espera `base`;
    después se duplica hasta `tope`. No baja de `base`."""
    if intento < 1:
        return base
    return min(tope, base * (2 ** (intento - 1)))


def directorio_estado(base: Path | None = None) -> Path:
    """Raíz de estado. La misma función que `rutas_estado.raiz`; este
    proceso solo le agrega `sip_stream/`."""
    if base is not None:
        return Path(base)
    from momentum_hunter.rutas_estado import raiz

    return raiz()


def directorio_stream(base: Path | None = None) -> Path:
    return directorio_estado(base) / "sip_stream"


def afirmar_fuera_del_repo(path: Path, repo: Path | None = None) -> Path:
    """El almacén no puede vivir dentro del checkout: un git pull lo
    pisaría o lo commitearía."""
    raiz = repo if repo is not None else Path(__file__).resolve().parents[2]
    try:
        path.resolve().relative_to(raiz.resolve())
    except ValueError:
        return path
    raise EstadoDentroDelRepo("el almacén SIP no puede vivir dentro del repo")


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="milliseconds")


def parse_instante(valor: object) -> datetime | None:
    """Timestamp con zona a UTC. Sin zona no se asume UTC."""
    if not isinstance(valor, str) or not valor.strip():
        return None
    texto = valor.strip()
    if texto.endswith("Z"):
        texto = texto[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(texto)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(UTC)


def minuto_iso(valor: object) -> str | None:
    """Minuto UTC de una vela, alineado al formato del REST
    (`+00:00`, sin segundos). Un reloj sin zona no se acomoda a un
    minuto: no hay vela."""
    dt = parse_instante(valor)
    if dt is None:
        return None
    dt = dt.astimezone(UTC).replace(second=0, microsecond=0)
    return dt.isoformat(timespec="seconds")


def fecha_ny(valor: object) -> str | None:
    dt = parse_instante(valor)
    if dt is None:
        return None
    return dt.astimezone(_NY).date().isoformat()


def _numero(valor: object) -> float | None:
    """None si el campo no vino. Un bool no es un precio. Un cero
    presente sí se conserva."""
    if isinstance(valor, bool) or valor is None:
        return None
    try:
        n = float(valor)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if n != n or n in (float("inf"), float("-inf")):
        return None
    return n


def _texto(valor: object) -> str | None:
    if not isinstance(valor, str):
        return None
    s = valor.strip()
    return s if s else None


def decodificar_frame(frame: bytes | str | None) -> list[dict] | None:
    """Lista de objetos JSON. El stream de datos manda un array.

    Un objeto suelto no se promociona a lista: no es el formato del
    server y no hay que adivinar. Bytes que no son UTF-8 y un JSON
    que no sea una lista de objetos devuelven None."""
    if isinstance(frame, bytes):
        try:
            texto = frame.decode("utf-8")
        except UnicodeDecodeError:
            return None
    elif isinstance(frame, str):
        texto = frame
    else:
        return None
    if not texto.strip():
        return None
    try:
        msg = json.loads(texto)
    except json.JSONDecodeError:
        return None
    if not isinstance(msg, list):
        return None
    if any(not isinstance(item, dict) for item in msg):
        return None
    return msg


def clasificar(msg: dict) -> str:
    tipo = msg.get("T") if isinstance(msg, dict) else None
    if tipo == "success":
        cual = msg.get("msg")
        if cual == "connected":
            return "connected"
        if cual == "authenticated":
            return "authenticated"
        return "success_otro"
    if tipo == "error":
        return "error"
    if tipo == "subscription":
        return "subscription"
    if tipo == "b":
        return "bar"
    if tipo == "u":
        return "updated_bar"
    return "otro"


def codigo_error(msg: dict) -> int | None:
    """El código numérico, o None si no vino. No se lee `msg`: el
    texto no se registra."""
    if not isinstance(msg, dict):
        return None
    codigo = msg.get("code")
    if isinstance(codigo, bool) or codigo is None:
        return None
    if isinstance(codigo, int):
        return codigo
    if isinstance(codigo, str) and codigo.strip().isdigit():
        return int(codigo.strip())
    return None


def barra_de_mensaje(msg: dict, recibido_en: str) -> dict | None:
    """Una vela de minuto, o None si falta símbolo, tiempo u OHLCV.

    El volumen ausente no se reemplaza por 0. `n` y `vw` pueden no
    venir: quedan null y no se usan para decidir. `T=u` es la misma
    vela corregida, no una vela nueva."""
    if not isinstance(msg, dict) or clasificar(msg) not in ("bar", "updated_bar"):
        return None
    simbolo = _texto(msg.get("S"))
    minuto = minuto_iso(msg.get("t"))
    o, h, lo, c, v = (
        _numero(msg.get("o")), _numero(msg.get("h")), _numero(msg.get("l")),
        _numero(msg.get("c")), _numero(msg.get("v")),
    )
    if simbolo is None or minuto is None or None in (o, h, lo, c, v):
        return None
    return {
        "tipo": "barra",
        "S": simbolo.upper(),
        "t": minuto,
        "o": o,
        "h": h,
        "l": lo,
        "c": c,
        "v": v,
        "n": _numero(msg.get("n")),
        "vw": _numero(msg.get("vw")),
        "correccion": clasificar(msg) == "updated_bar",
        "recibido_en": recibido_en,
    }


def mensaje_auth(api_key: str, api_secret: str) -> dict:
    return {"action": "auth", "key": api_key, "secret": api_secret}


def mensaje_subscribe(queries: list[str], *, correcciones: bool = True) -> dict:
    """Solo barras. `updatedBars` es la corrección del mismo minuto.
    No se piden trades ni quotes, y no se manda `*`."""
    limpios = [q for q in queries if isinstance(q, str) and q and q != "*"]
    msg: dict = {"action": "subscribe", "bars": limpios}
    if correcciones:
        msg["updatedBars"] = list(limpios)
    return msg


def mensaje_unsubscribe(queries: list[str]) -> dict:
    limpios = [q for q in queries if isinstance(q, str) and q and q != "*"]
    return {"action": "unsubscribe", "bars": limpios, "updatedBars": limpios}


def cazar_wildcard(msg: dict) -> bool:
    if not isinstance(msg, dict) or clasificar(msg) != "subscription":
        return False
    for canal in ("bars", "updatedBars", "trades", "quotes"):
        lista = msg.get(canal)
        if isinstance(lista, list) and "*" in lista:
            return True
    return False


def hunters_confirmados(msg: dict, mapa: dict[str, str]) -> list[str] | None:
    """Símbolos de la watchlist que el server dejó en `bars`.

    None si el mensaje no es una confirmación usable (incluido `*`).
    Una lista vacía es una confirmación real de "no hay nadie": no es
    lo mismo que None, que es "todavía no contestó"."""
    if not isinstance(msg, dict) or clasificar(msg) != "subscription":
        return None
    if cazar_wildcard(msg):
        return None
    barras = msg.get("bars")
    if not isinstance(barras, list):
        return None
    inverso: dict[str, list[str]] = {}
    for hunter, query in mapa.items():
        if isinstance(hunter, str) and isinstance(query, str):
            inverso.setdefault(query.upper(), []).append(hunter.upper())
    out: list[str] = []
    vistos: set[str] = set()
    for q in barras:
        if not isinstance(q, str) or not q.strip() or q.strip() == "*":
            continue
        for h in inverso.get(q.strip().upper(), [q.strip().upper()]):
            if h not in vistos:
                vistos.add(h)
                out.append(h)
    return out


def elegir_simbolos(
    filas: list[tuple[str, str]],
    posiciones: list[str] | None,
    *,
    tope: int = SIMBOLOS_TOPE,
) -> tuple[list[str], list[str]]:
    """(suscribir, fuera_de_tope).

    Primero la posición abierta, después TRIGGERED, después WATCHING.
    Otro estado no es interés. `posiciones is None` no aporta tickers
    (no es "cero posiciones": el caller lo marca aparte). Un ticker
    repetido entra una vez."""
    def _limpio(ticker: object) -> str | None:
        if not isinstance(ticker, str):
            return None
        s = ticker.strip().upper()
        return s if s else None

    grupos: list[list[str]] = [[], [], []]
    if posiciones is not None:
        for t in posiciones:
            u = _limpio(t)
            if u:
                grupos[0].append(u)
    for ticker, estado in filas:
        u = _limpio(ticker)
        if u is None or not isinstance(estado, str):
            continue
        e = estado.strip().lower()
        if e == "triggered":
            grupos[1].append(u)
        elif e == "watching":
            grupos[2].append(u)
    orden: list[str] = []
    vistos: set[str] = set()
    for grupo in grupos:
        for s in grupo:
            if s in vistos:
                continue
            vistos.add(s)
            orden.append(s)
    if len(orden) <= tope:
        return orden, []
    return orden[:tope], orden[tope:]


def traducir(tickers: list[str]) -> tuple[dict[str, str], list[str]]:
    """Mapa hunter → query de Alpaca, y los que no se mandan.

    El sufijo sin traducción verificada no se adivina (`_simbolo_para_query`).
    Esos quedan fuera: el lector los ve como no suscritos y el primario
    cae al REST."""
    mapa: dict[str, str] = {}
    sin: list[str] = []
    for t in tickers:
        if not isinstance(t, str) or not t.strip():
            continue
        clave = t.strip().upper()
        query = _simbolo_para_query(clave)
        if query is None:
            sin.append(clave)
            continue
        mapa[clave] = query
    return mapa, sin


@dataclass
class Universo:
    watchlist: str
    posiciones: str
    simbolos: list[str] = field(default_factory=list)
    fuera_de_tope: list[str] = field(default_factory=list)
    sin_traduccion: list[str] = field(default_factory=list)
    mapa: dict[str, str] = field(default_factory=dict)

    @property
    def queries(self) -> list[str]:
        vistos: list[str] = []
        ya: set[str] = set()
        for h in self.simbolos:
            q = self.mapa.get(h)
            if not q or q in ya:
                continue
            ya.add(q)
            vistos.append(q)
        return vistos


def _clasificar_archivo(path: Path) -> tuple[str, object | None]:
    if not path.is_file():
        return "ausente", None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "ilegible", None
    return "presente", data


def _filas_watchlist(data: object) -> tuple[str, list[tuple[str, str]]]:
    """`leida` solo si cada entrada se pudo leer. Una entrada rota no
    se tira en silencio: el universo queda ilegible y el primario no
    decide con una lista a la que le falta un ticker."""
    if not isinstance(data, dict) or not isinstance(data.get("entradas"), list):
        return "ilegible", []
    from momentum_hunter import watchlist

    crudas = data["entradas"]
    if any(not isinstance(d, dict) for d in crudas):
        return "ilegible", []
    try:
        entradas = watchlist.parsear(data)
    except (TypeError, ValueError):
        return "ilegible", []
    if len(entradas) != len(crudas):
        return "ilegible", [(e.ticker, e.estado) for e in entradas if isinstance(e.ticker, str)]
    return "leida", [(e.ticker, e.estado) for e in entradas if isinstance(e.ticker, str)]


def _aplicar_overlay(filas: list[tuple[str, str]], data_wl: dict, overlay_path: Path) -> tuple[str, list[tuple[str, str]]]:
    est, data = _clasificar_archivo(overlay_path)
    if est == "ausente":
        return "leida", filas
    if est != "presente" or not isinstance(data, dict):
        return "ilegible", filas
    if data.get("schema") not in (None, 1) or not isinstance(data.get("entries"), dict):
        return "ilegible", filas
    from momentum_hunter import watchlist

    try:
        entradas = watchlist.aplicar_overlay(watchlist.parsear(data_wl), overlay_path)
    except (TypeError, ValueError, OSError):
        return "ilegible", filas
    return "leida", [(e.ticker, e.estado) for e in entradas if isinstance(e.ticker, str)]


def _tickers_abiertos(data: object) -> tuple[str, list[str] | None]:
    """`resultado == abierta` y `entro is True`. Un resultado ausente
    no es una posición, y un archivo que no es el libro no es cero
    posiciones."""
    if not isinstance(data, dict) or not isinstance(data.get("revisiones"), list):
        return "ilegible", None
    out: list[str] = []
    for rev in data["revisiones"]:
        if not isinstance(rev, dict):
            return "ilegible", None
        if rev.get("resultado") != "abierta" or rev.get("entro") is not True:
            continue
        t = rev.get("ticker")
        if isinstance(t, str) and t.strip():
            out.append(t.strip().upper())
    return "leida", out


def cargar_universo(
    *,
    watchlist_path: Path,
    revisiones_path: Path,
    overlay_path: Path | None,
    tope: int = SIMBOLOS_TOPE,
) -> Universo:
    est_w, data_w = _clasificar_archivo(watchlist_path)
    if est_w == "presente":
        estado_w, filas = _filas_watchlist(data_w)
        if estado_w == "leida" and overlay_path is not None and isinstance(data_w, dict):
            estado_w, filas = _aplicar_overlay(filas, data_w, overlay_path)
        elif estado_w != "leida":
            estado_w = "ilegible"
    elif est_w == "ausente":
        estado_w, filas = "ausente", []
    else:
        estado_w, filas = "ilegible", []

    est_p, data_p = _clasificar_archivo(revisiones_path)
    if est_p == "presente":
        estado_p, posiciones = _tickers_abiertos(data_p)
    elif est_p == "ausente":
        estado_p, posiciones = "ausente", None
    else:
        estado_p, posiciones = "ilegible", None

    simbolos, fuera = elegir_simbolos(filas, posiciones, tope=tope)
    mapa, sin = traducir(simbolos)
    # Los que no se pueden pedir tampoco están suscritos.
    suscribir = [s for s in simbolos if s in mapa]
    return Universo(
        watchlist=estado_w,
        posiciones=estado_p,
        simbolos=suscribir,
        fuera_de_tope=fuera + [s for s in simbolos if s not in mapa],
        sin_traduccion=sin,
        mapa=mapa,
    )


def rutas_universo(estado_raiz: Path | None = None) -> tuple[Path, Path, Path]:
    """Watchlist, revisiones y overlay.

    Desde #200 las dos primeras viven bajo `MOMENTUM_ESTADO_DIR`
    (`momentum_hunter/watchlist.json` y
    `momentum_paper_trader/revisiones.json`). `resolver` copia el
    legado del repo una sola vez si el destino no existe y la
    migración está encendida. Si no hay archivo, la ruta igual apunta
    al destino: ausente no se lee del checkout ni cuenta como cero
    posiciones. El overlay del VPS no se movió: sigue en
    `/var/lib/momentum/watchlist_vps_state.json`."""
    from momentum_hunter import watchlist
    from momentum_hunter.rutas_estado import resolver

    if estado_raiz is not None:
        raiz = Path(estado_raiz)
        wl = raiz / "momentum_hunter" / "watchlist.json"
        rev = raiz / "momentum_paper_trader" / "revisiones.json"
    else:
        wl = resolver("momentum_hunter/watchlist.json")
        rev = resolver("momentum_paper_trader/revisiones.json")
    return wl, rev, watchlist.state_path()


def _secretos_utiles(secretos: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(s for s in secretos if isinstance(s, str) and len(s) >= _LARGO_MINIMO_SECRETO)


def _redactar(valor, secretos: tuple[str, ...]):
    utiles = _secretos_utiles(secretos)
    if isinstance(valor, str):
        out = valor
        for s in utiles:
            if s in out:
                out = out.replace(s, "***")
        return out
    if isinstance(valor, dict):
        return {k: _redactar(v, secretos) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_redactar(v, secretos) for v in valor]
    return valor


def _estado_inicial() -> dict:
    return {
        "version": VERSION_ESTADO,
        "modo": None,
        "conectado": False,
        "autenticado": False,
        "conectado_desde": None,
        "ultimo_frame_en": None,
        "ultimo_heartbeat_en": None,
        "suscritos": None,
        "fuera_de_tope": [],
        "sin_traduccion": [],
        "watchlist": None,
        "posiciones": None,
        "hueco_abierto": False,
        "huecos": [],
        "error_codigo": None,
        "n_simbolos": None,
    }


def _cargar_estado(path: Path) -> dict:
    if not path.exists():
        return _estado_inicial()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        raise EstadoIlegible from ex
    if not isinstance(data, dict) or data.get("version") != VERSION_ESTADO:
        raise EstadoIlegible
    base = _estado_inicial()
    base.update(data)
    base["version"] = VERSION_ESTADO
    if not isinstance(base.get("huecos"), list):
        raise EstadoIlegible
    return base


class Almacen:
    """JSONL de barras por día (NY) y `estado.json` atómico. El candado
    es del proceso: una sola conexión."""

    def __init__(self, raiz: Path, estado: dict, lock, secretos: tuple[str, ...]) -> None:
        self.raiz = raiz
        self.estado = estado
        self._lock = lock
        self._secretos = secretos
        self.al_anotar = None

    @classmethod
    def abrir(cls, raiz: Path, secretos: tuple[str, ...] = (), repo: Path | None = None) -> Almacen:
        afirmar_fuera_del_repo(raiz, repo)
        raiz.mkdir(parents=True, exist_ok=True)
        (raiz / "barras").mkdir(exist_ok=True)
        (raiz / "eventos").mkdir(exist_ok=True)
        (raiz / "telemetria").mkdir(exist_ok=True)
        lock_path = raiz / "sip_stream.lock"
        lock = open(lock_path, "a+")
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise YaCorre
        estado_path = raiz / "estado.json"
        try:
            estado = _cargar_estado(estado_path)
        except Exception:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()
            raise
        alm = cls(raiz, estado, lock, secretos)
        if not estado_path.exists():
            alm._volcar_estado()
        return alm

    def anotar(self, registro: dict, *, dia: str | None = None, clase: str = "eventos") -> None:
        limpio = _redactar(registro, self._secretos)
        cuando = dia or fecha_ny(limpio.get("recibido_en") or limpio.get("t"))
        if cuando is None:
            cuando = datetime.now(UTC).astimezone(_NY).date().isoformat()
        carpeta = "barras" if clase == "barras" else "eventos"
        path = self.raiz / carpeta / f"{cuando}.jsonl"
        linea = json.dumps(limpio, ensure_ascii=False, separators=(",", ":"))
        with path.open("a", encoding="utf-8") as f:
            f.write(linea + "\n")
            f.flush()
            os.fsync(f.fileno())
        if self.al_anotar is not None:
            self.al_anotar(limpio)

    def anotar_barra(self, barra: dict) -> None:
        self.anotar(barra, dia=fecha_ny(barra.get("t")), clase="barras")

    def anotar_telemetria(self, registro: dict, dia: str) -> None:
        path = self.raiz / "telemetria" / f"{dia}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        limpio = _redactar(registro, self._secretos)
        linea = json.dumps(limpio, ensure_ascii=False, separators=(",", ":"))
        with path.open("a", encoding="utf-8") as f:
            f.write(linea + "\n")
            f.flush()
            os.fsync(f.fileno())

    def _podar_huecos(self, ahora: datetime) -> None:
        limite = ahora - _RETENCION_HUECOS
        vivos = []
        for h in self.estado.get("huecos") or []:
            if not isinstance(h, dict):
                continue
            hasta = parse_instante(h.get("hasta"))
            desde = parse_instante(h.get("desde"))
            if hasta is not None and hasta < limite:
                continue
            if hasta is None and desde is not None and desde < limite and not self.estado.get("hueco_abierto"):
                continue
            vivos.append(h)
        self.estado["huecos"] = vivos

    def _volcar_estado(self) -> None:
        path = self.raiz / "estado.json"
        tmp = path.with_name("estado.json.tmp")
        tmp.write_text(
            json.dumps(self.estado, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)

    def guardar(self) -> None:
        self._volcar_estado()

    def abrir_hueco(self, ahora: datetime, motivo: str) -> dict:
        if self.estado.get("hueco_abierto"):
            return {}
        reg = {
            "tipo": "hueco",
            "motivo": motivo,
            "recibido_en": _iso(ahora),
            "desde": _iso(ahora),
            "hasta": None,
        }
        self.estado["hueco_abierto"] = True
        self.estado["conectado"] = False
        huecos = list(self.estado.get("huecos") or [])
        huecos.append({"desde": reg["desde"], "hasta": None, "motivo": motivo})
        self.estado["huecos"] = huecos
        self._podar_huecos(ahora)
        self.anotar(reg)
        self._volcar_estado()
        return reg

    def cerrar_hueco(self, ahora: datetime) -> dict | None:
        if not self.estado.get("hueco_abierto"):
            return None
        self.estado["hueco_abierto"] = False
        hasta = _iso(ahora)
        for h in reversed(self.estado.get("huecos") or []):
            if isinstance(h, dict) and h.get("hasta") is None:
                h["hasta"] = hasta
                break
        reg = {"tipo": "hueco_cerrado", "recibido_en": hasta, "hasta": hasta}
        self.anotar(reg)
        return reg

    def marcar_conectado(self, ahora: datetime, suscritos: list[str] | None, universo: Universo, modo: str) -> None:
        self.estado["modo"] = modo
        self.estado["conectado"] = True
        self.estado["autenticado"] = True
        self.estado["error_codigo"] = None
        if self.estado.get("conectado_desde") is None or self.estado.get("hueco_abierto"):
            # Un hueco abierto corta la racha. La cobertura empieza de
            # nuevo: lo de antes del hueco no alcanza para un pedido
            # que lo atraviesa, y `conectado_desde` es el inicio de
            # ESTA racha limpia.
            if self.estado.get("hueco_abierto"):
                self.cerrar_hueco(ahora)
            self.estado["conectado_desde"] = _iso(ahora)
        self.estado["ultimo_frame_en"] = _iso(ahora)
        self.estado["ultimo_heartbeat_en"] = _iso(ahora)
        self.estado["suscritos"] = list(suscritos) if suscritos is not None else None
        self.estado["n_simbolos"] = None if suscritos is None else len(suscritos)
        self.estado["fuera_de_tope"] = list(universo.fuera_de_tope)
        self.estado["sin_traduccion"] = list(universo.sin_traduccion)
        self.estado["watchlist"] = universo.watchlist
        self.estado["posiciones"] = universo.posiciones
        self.estado["mapa"] = dict(universo.mapa)
        self._podar_huecos(ahora)
        self._volcar_estado()

    def marcar_desconectado(self, ahora: datetime) -> None:
        self.estado["conectado"] = False
        self.estado["autenticado"] = False
        self._volcar_estado()

    def latido(self, ahora: datetime, silencio_s: float | None) -> None:
        self.estado["ultimo_heartbeat_en"] = _iso(ahora)
        self._volcar_estado()
        self.anotar({
            "tipo": "heartbeat",
            "recibido_en": _iso(ahora),
            "silencio_s": silencio_s,
            "conectado": self.estado.get("conectado") is True,
        })

    def cerrar(self) -> None:
        if self._lock is None:
            return
        try:
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
        finally:
            self._lock.close()
            self._lock = None


def leer_estado(directorio: Path) -> dict | None:
    """None si no hay archivo o no se puede leer. No es un estado
    vacío: vacío significaría "desconectado y sin huecos", y eso no
    es lo que dice un archivo que falta."""
    path = directorio / "estado.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("version") != VERSION_ESTADO:
        return None
    return data


def _hueco_solapa(hueco: dict, inicio: datetime, fin: datetime) -> bool:
    desde = parse_instante(hueco.get("desde")) if isinstance(hueco, dict) else None
    if desde is None:
        return True
    hasta = parse_instante(hueco.get("hasta")) if hueco.get("hasta") else None
    fin_hueco = hasta or datetime.max.replace(tzinfo=UTC)
    return desde < fin and fin_hueco > inicio


def motivo_no_cubre(
    estado: dict | None,
    tickers: list[str],
    inicio: datetime,
    fin: datetime,
    ahora: datetime,
) -> str | None:
    """None si el almacén puede reemplazar al REST para este pedido.

    Cualquier duda (viejo, caído, hueco, símbolo no confirmado, archivo
    de universo que no se pudo leer) devuelve el motivo y el caller
    sigue por REST. No hay un motivo "cero barras"."""
    if not isinstance(estado, dict):
        return "ausente"
    if estado.get("conectado") is not True:
        return "desconectado"
    if estado.get("hueco_abierto") is True:
        return "hueco"
    hb = parse_instante(estado.get("ultimo_heartbeat_en"))
    if hb is None:
        return "sin_latido"
    if (ahora - hb).total_seconds() > FRESCURA_MAX_S:
        return "viejo"
    desde = parse_instante(estado.get("conectado_desde"))
    if desde is None or desde > inicio:
        return "incompleto"
    if estado.get("watchlist") != "leida" or estado.get("posiciones") != "leida":
        return "incompleto"
    suscritos = estado.get("suscritos")
    if not isinstance(suscritos, list):
        return "suscripcion_desconocida"
    pedidas = {
        t.strip().upper() for t in tickers if isinstance(t, str) and t.strip()
    }
    tienen = {s.strip().upper() for s in suscritos if isinstance(s, str)}
    if not pedidas.issubset(tienen):
        return "incompleto"
    fuera = estado.get("fuera_de_tope")
    if not isinstance(fuera, list):
        return "incompleto"
    if any(isinstance(s, str) and s.strip().upper() in pedidas for s in fuera):
        return "incompleto"
    for hueco in estado.get("huecos") or []:
        if isinstance(hueco, dict) and _hueco_solapa(hueco, inicio, fin):
            return "hueco"
    return None


def _dias_ny(inicio: datetime, fin: datetime) -> list[str]:
    cursor = inicio.astimezone(_NY).date()
    ultimo = fin.astimezone(_NY).date()
    dias = []
    while cursor <= ultimo:
        dias.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return dias


def leer_barras(directorio: Path, inicio: datetime, fin: datetime) -> tuple[list[dict], int]:
    """(líneas de barra reducibles, líneas ilegibles).

    La última línea de un (símbolo, minuto) gana: así entra la
    corrección de `updatedBars` sin borrar el minuto. Una línea que
    no parsea se cuenta y no se convierte en una vela de ceros."""
    malas = 0
    crudas: list[dict] = []
    if not directorio.is_dir():
        return [], 0
    for dia in _dias_ny(inicio, fin):
        path = directorio / "barras" / f"{dia}.jsonl"
        if not path.is_file():
            continue
        try:
            texto = path.read_text(encoding="utf-8")
        except OSError:
            malas += 1
            continue
        for linea in texto.splitlines():
            if not linea.strip():
                continue
            try:
                obj = json.loads(linea)
            except json.JSONDecodeError:
                malas += 1
                continue
            if not isinstance(obj, dict) or obj.get("tipo") != "barra":
                malas += 1
                continue
            crudas.append(obj)
    reducidas: dict[tuple[str, str], dict] = {}
    for obj in crudas:
        simbolo = obj.get("S")
        minuto = minuto_iso(obj.get("t"))
        if not isinstance(simbolo, str) or minuto is None:
            malas += 1
            continue
        if None in (
            _numero(obj.get("o")), _numero(obj.get("h")), _numero(obj.get("l")),
            _numero(obj.get("c")), _numero(obj.get("v")),
        ):
            malas += 1
            continue
        reducidas[(simbolo.upper(), minuto)] = obj
    dentro = []
    for (_s, minuto), obj in reducidas.items():
        dt = parse_instante(minuto)
        if dt is None:
            continue
        if inicio <= dt <= fin:
            dentro.append(obj)
    return dentro, malas


def _a_intradia(ticker: str, filas: list[dict]) -> BarraIntradia | None:
    velas = []
    for obj in filas:
        minuto = parse_instante(minuto_iso(obj.get("t")))
        o, h, lo, c, v = (
            _numero(obj.get("o")), _numero(obj.get("h")), _numero(obj.get("l")),
            _numero(obj.get("c")), _numero(obj.get("v")),
        )
        if minuto is None or None in (o, h, lo, c, v):
            return None
        velas.append((minuto, o, h, lo, c, v))
    if not velas:
        return None
    marcas, o, h, lo, c, vol = _listas(velas, intradia=True)
    if len(marcas) < 5:
        return None
    return BarraIntradia(ticker, marcas, o, c, h, lo, vol)


def barras_si_cubren(
    tickers: list[str],
    intervalo: str = "1m",
    periodo: str = "5d",
    *,
    directorio: Path | None = None,
    ahora: datetime | None = None,
) -> dict[str, BarraIntradia] | None:
    """Velas de minuto del almacén, o None si hay que ir al REST.

    None no es "no hubo velas". Es "este pedido no se puede servir
    desde el stream". Solo corre con `MOMENTUM_SIP_STREAM=primario`;
    en sombra ni abre el directorio. Una línea ilegible tumba el
    pedido entero: una serie a la que le falta una vela movería el
    VWAP, y un hueco no se rellena con ceros."""
    if modo_configurado() != "primario":
        return None
    if (intervalo or "").strip().lower() not in ("1m", "1min"):
        return None
    from momentum_hunter.data.alpaca_datos import dias_de_periodo, ErrorDatosAlpaca

    reloj = ahora or datetime.now(UTC)
    if reloj.tzinfo is None:
        reloj = reloj.replace(tzinfo=UTC)
    try:
        inicio = reloj - timedelta(days=dias_de_periodo(periodo))
    except ErrorDatosAlpaca:
        return None
    raiz = directorio if directorio is not None else directorio_stream()
    estado = leer_estado(raiz)
    motivo = motivo_no_cubre(estado, tickers, inicio, reloj, reloj)
    if motivo is not None:
        log.info("stream SIP no cubre el pedido (%s); sigue el REST", motivo)
        return None
    filas, malas = leer_barras(raiz, inicio, reloj)
    if malas:
        log.info("stream SIP con %d línea(s) ilegible(s); sigue el REST", malas)
        return None
    por_ticker: dict[str, list[dict]] = {}
    for obj in filas:
        s = obj.get("S")
        if isinstance(s, str):
            por_ticker.setdefault(s.upper(), []).append(obj)
    pedidos = [t for t in tickers if isinstance(t, str) and t.strip()]
    out: dict[str, BarraIntradia] = {}
    for t in pedidos:
        serie = _a_intradia(t, por_ticker.get(t.strip().upper(), []))
        if serie is not None:
            out[t] = serie
    # Una serie corta no se entrega como si fuera la cinta: el REST
    # puede tener los minutos que acá no están. Todo el pedido vuelve
    # al REST; no se mezcla una parte stream con el resto ausente.
    if any(t not in out for t in pedidos):
        log.info("stream SIP sin serie usable para todo el pedido; sigue el REST")
        return None
    return out


class Vigilancia:
    """Heartbeat mientras el socket calla. El silencio no abre hueco:
    lo abre la caída, en el bucle de reconexión."""

    def __init__(self, heartbeat_s: float = HEARTBEAT_S) -> None:
        self.heartbeat_s = heartbeat_s
        self.ultimo: datetime | None = None
        self.ultimo_heartbeat: datetime | None = None
        self._ultimo_minuto: dict[str, datetime] = {}

    def silencio_s(self, ahora: datetime) -> float | None:
        if self.ultimo is None:
            return None
        return round((ahora - self.ultimo).total_seconds(), 3)

    def recibido(self, ahora: datetime) -> None:
        self.ultimo = ahora
        self.ultimo_heartbeat = ahora

    def salto(self, simbolo: str, minuto: datetime) -> dict | None:
        """Un agujero entre dos velas del mismo símbolo. No inventa las
        intermedias y no las escribe con volumen 0. Alpaca omite el
        minuto sin trades: esto es telemetría, no una vela."""
        previo = self._ultimo_minuto.get(simbolo)
        self._ultimo_minuto[simbolo] = minuto
        if previo is None:
            return None
        delta = (minuto - previo).total_seconds()
        if delta <= 60:
            return None
        return {
            "tipo": "salto",
            "S": simbolo,
            "desde": previo.astimezone(UTC).isoformat(timespec="seconds"),
            "hasta": minuto.astimezone(UTC).isoformat(timespec="seconds"),
            "salto_s": delta,
        }

    def revisar(self, ahora: datetime) -> list[dict]:
        if self.ultimo is None:
            self.ultimo = ahora
            self.ultimo_heartbeat = ahora
            return []
        silencio = (ahora - self.ultimo).total_seconds()
        if (
            self.ultimo_heartbeat is None
            or (ahora - self.ultimo_heartbeat).total_seconds() >= self.heartbeat_s
        ):
            self.ultimo_heartbeat = ahora
            return [{
                "tipo": "heartbeat",
                "recibido_en": _iso(ahora),
                "silencio_s": round(silencio, 3),
                "conectado": True,
            }]
        return []


def _log_marca(reg: dict) -> None:
    tipo = reg.get("tipo")
    if tipo == "heartbeat":
        log.info("heartbeat sip silencio_s=%s", reg.get("silencio_s"))
    elif tipo == "hueco":
        log.warning("hueco sip motivo=%s", reg.get("motivo"))
    elif tipo == "salto":
        log.info("salto sip %s salto_s=%s", reg.get("S"), reg.get("salto_s"))


class _SocketWebsockets:
    def __init__(self, cm, ws) -> None:
        self._cm = cm
        self._ws = ws

    async def enviar(self, texto: str) -> None:
        await self._ws.send(texto)

    async def recibir(self):
        try:
            return await self._ws.recv()
        except Exception as ex:
            if type(ex).__name__.startswith("ConnectionClosed"):
                raise ConexionCerrada from None
            raise

    async def cerrar(self) -> None:
        try:
            await self._cm.__aexit__(None, None, None)
        except Exception as ex:
            if type(ex).__name__.startswith("ConnectionClosed"):
                return
            log.info("al cerrar el stream (%s)", type(ex).__name__)


async def conectar_sip(url: str) -> _SocketWebsockets:
    afirmar_url_sip(url)
    try:
        import websockets
    except ImportError as ex:
        raise RuntimeError("falta el paquete websockets") from ex
    cm = websockets.connect(
        url,
        open_timeout=10,
        ping_interval=20,
        ping_timeout=20,
        max_queue=4096,
        compression=None,
    )
    ws = await cm.__aenter__()
    return _SocketWebsockets(cm, ws)


async def _recibir(sock, timeout: float, parada: asyncio.Event):
    recv_task = asyncio.create_task(sock.recibir())
    stop_task = asyncio.create_task(parada.wait())
    try:
        done, _pending = await asyncio.wait(
            {recv_task, stop_task},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if recv_task in done:
            return recv_task.result()
        if stop_task in done:
            raise ParadaPedida
        return None
    finally:
        for t in (recv_task, stop_task):
            if not t.done():
                t.cancel()
        await asyncio.gather(recv_task, stop_task, return_exceptions=True)


async def _cerrar(sock) -> None:
    if sock is None:
        return
    try:
        await sock.cerrar()
    except Exception as ex:
        log.info("al cerrar el socket (%s)", type(ex).__name__)


def _anotar(almacen: Almacen, registro: dict, *, clase: str = "eventos") -> str | None:
    try:
        if clase == "barras":
            almacen.anotar_barra(registro)
        else:
            almacen.anotar(registro)
    except OSError as ex:
        log.error("no se pudo escribir el stream SIP (%s)", type(ex).__name__)
        return "disco"
    _log_marca(registro)
    return None


def _universo_vacio() -> Universo:
    return Universo(watchlist="ausente", posiciones="ausente")


async def _hasta(sock, parada, timeout: float, pred) -> tuple[str, list[dict]]:
    """Lee frames hasta que `pred` acepta un mensaje, o se acaba el tiempo.

    Devuelve (`ok` | `timeout` | `caida` | `parada` | `error` | `ilegible`,
    mensajes vistos). `error` trae el mensaje de error en la lista."""
    vistos: list[dict] = []
    while True:
        try:
            frame = await _recibir(sock, timeout, parada)
        except ParadaPedida:
            return "parada", vistos
        except ConexionCerrada:
            return "caida", vistos
        except Exception as ex:
            log.warning("recv falló (%s)", type(ex).__name__)
            return "caida", vistos
        if frame is None:
            return "timeout", vistos
        msgs = decodificar_frame(frame)
        if msgs is None:
            return "ilegible", vistos
        # El frame entero se lee: un array puede traer la confirmación
        # y una vela juntas, y cortar a la mitad perdería la vela.
        cumplido = False
        for msg in msgs:
            vistos.append(msg)
            if clasificar(msg) == "error":
                return "error", vistos
            if pred(msg):
                cumplido = True
        if cumplido:
            return "ok", vistos


def _diff_queries(previas: list[str], nuevas: list[str]) -> tuple[list[str], list[str]]:
    a, b = set(previas), set(nuevas)
    return sorted(b - a), sorted(a - b)


async def _consumir(
    sock, almacen, vigilancia, parada, reloj, universo_fn, modo: str,
    espera_recepcion: float, queries_activas: list[str],
) -> str:
    while not parada.is_set():
        try:
            frame = await _recibir(sock, espera_recepcion, parada)
        except ParadaPedida:
            return "parada"
        except ConexionCerrada:
            return "caida"
        except Exception as ex:
            log.warning("recv falló (%s)", type(ex).__name__)
            return "caida"
        ahora = reloj()
        if frame is None:
            for reg in vigilancia.revisar(ahora):
                silencio = reg.get("silencio_s")
                try:
                    # `latido` ya appendea el heartbeat y avisa a `al_anotar`.
                    almacen.latido(ahora, silencio if isinstance(silencio, (int, float)) else None)
                except OSError:
                    return "disco"
            universo = universo_fn()
            altas, bajas = _diff_queries(queries_activas, universo.queries)
            if altas or bajas:
                if bajas:
                    await sock.enviar(json.dumps(mensaje_unsubscribe(bajas)))
                if altas:
                    await sock.enviar(json.dumps(mensaje_subscribe(altas)))
                queries_activas[:] = list(universo.queries)
                almacen.estado["watchlist"] = universo.watchlist
                almacen.estado["posiciones"] = universo.posiciones
                almacen.estado["fuera_de_tope"] = list(universo.fuera_de_tope)
                almacen.estado["mapa"] = dict(universo.mapa)
                try:
                    almacen.guardar()
                except OSError:
                    return "disco"
            if parada.is_set():
                return "parada"
            continue
        msgs = decodificar_frame(frame)
        if msgs is None:
            log.warning("frame ilegible; no se inventa una vela")
            continue
        vigilancia.recibido(ahora)
        almacen.estado["ultimo_frame_en"] = _iso(ahora)
        for msg in msgs:
            kind = clasificar(msg)
            if kind == "error":
                codigo = codigo_error(msg)
                almacen.estado["error_codigo"] = codigo
                log.warning("el stream SIP mandó un error (%s)", codigo)
                if codigo == 406:
                    return "limite_conexion"
                if codigo == 405:
                    return "limite_simbolos"
                if codigo in _CODIGOS_AUTH or codigo == 409:
                    return "auth"
                return "caida"
            if kind == "subscription":
                if cazar_wildcard(msg):
                    log.error("el server confirmó un wildcard; no se usa")
                    return "wildcard"
                mapa = almacen.estado.get("mapa") if isinstance(almacen.estado.get("mapa"), dict) else {}
                confirmados = hunters_confirmados(msg, mapa)
                almacen.estado["suscritos"] = confirmados
                almacen.estado["n_simbolos"] = None if confirmados is None else len(confirmados)
                try:
                    almacen.guardar()
                except OSError:
                    return "disco"
                continue
            if kind in ("bar", "updated_bar"):
                barra = barra_de_mensaje(msg, _iso(ahora))
                if barra is None:
                    log.warning("vela SIP incompleta; no se guarda un cero")
                    continue
                minuto = parse_instante(barra["t"])
                if minuto is not None:
                    salto = vigilancia.salto(barra["S"], minuto)
                    if salto is not None and _anotar(almacen, salto) == "disco":
                        return "disco"
                if _anotar(almacen, barra, clase="barras") == "disco":
                    return "disco"
                continue
        try:
            almacen.guardar()
        except OSError:
            return "disco"
    return "parada"


async def correr(
    *,
    url: str = URL_STREAM_SIP,
    directorio: Path | None = None,
    api_key: str | None = None,
    api_secret: str | None = None,
    conector=None,
    dormir=None,
    parada: asyncio.Event | None = None,
    vigilancia: Vigilancia | None = None,
    ahora=None,
    modo: str | None = None,
    universo_fn=None,
    espera_recepcion: float = HEARTBEAT_S,
    espera_auth: float = ESPERA_AUTH_S,
    max_fallos_auth: int = MAX_FALLOS_AUTH,
    al_anotar=None,
    instalar_senales: bool = False,
    comparar_cada_s: float | None = None,
    repo: Path | None = None,
) -> int:
    """Escucha hasta que pidan parar. 0 si la parada fue limpia o el
    flag es `0`. 1 si no se puede seguir (credenciales, auth, disco,
    otro proceso, almacén dentro del repo)."""
    afirmar_url_sip(url)
    modo_efectivo = modo if modo is not None else modo_configurado()
    if modo_efectivo == "0":
        log.info("MOMENTUM_SIP_STREAM=0; no se abre el socket de datos")
        return 0
    if modo_efectivo not in ("sombra", "primario"):
        modo_efectivo = "sombra"
    if not api_key or not api_secret:
        log.error("sin credenciales de Alpaca paper; no se conecta al stream de datos")
        return 1
    if parada is None:
        parada = asyncio.Event()
    if instalar_senales:
        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGTERM, parada.set)
            loop.add_signal_handler(signal.SIGINT, parada.set)
        except NotImplementedError:
            pass
    if conector is None:
        conector = conectar_sip
    if dormir is None:
        dormir = asyncio.sleep
    if vigilancia is None:
        vigilancia = Vigilancia()
    if ahora is None:
        def ahora():
            return datetime.now(UTC)
    if universo_fn is None:
        def universo_fn():
            wl, rev, overlay = rutas_universo()
            try:
                return cargar_universo(
                    watchlist_path=wl, revisiones_path=rev, overlay_path=overlay,
                )
            except (OSError, EstadoDentroDelRepo):
                return Universo(watchlist="ilegible", posiciones="ilegible")
    raiz = directorio if directorio is not None else directorio_stream()
    try:
        almacen = Almacen.abrir(raiz, secretos=(api_key, api_secret), repo=repo)
    except YaCorre:
        log.error("ya hay otro stream SIP; no se abre una segunda conexión")
        return 1
    except EstadoIlegible:
        log.error("estado.json del stream es ilegible; no se arranca ni se reescribe")
        return 1
    except EstadoDentroDelRepo:
        log.error("el almacén SIP caería dentro del repo; no se arranca")
        return 1
    except OSError as ex:
        log.error("no se pudo abrir el almacén SIP (%s)", type(ex).__name__)
        return 1
    almacen.al_anotar = al_anotar
    almacen.estado["modo"] = modo_efectivo
    try:
        almacen.guardar()
        # Un proceso anterior pudo morir dejando `conectado_desde` como
        # si la racha siguiera. El hueco de arranque corta esa racha:
        # el primario no hereda minutos que este proceso no vio.
        if almacen.estado.get("conectado_desde") or almacen.estado.get("conectado") is True:
            almacen.estado["conectado"] = False
            almacen.abrir_hueco(ahora(), "arranque")
    except OSError:
        almacen.cerrar()
        return 1

    fallos_auth = 0
    caidas = 0
    ultima_comparacion: datetime | None = None
    try:
        while not parada.is_set():
            sock = None
            codigo = "caida"
            try:
                sock = await conector(url)
                await sock.enviar(json.dumps(mensaje_auth(api_key, api_secret)))
                estado_auth, msgs = await _hasta(
                    sock, parada, espera_auth,
                    lambda m: clasificar(m) == "authenticated",
                )
                if estado_auth == "error":
                    codigo_err = codigo_error(msgs[-1]) if msgs else None
                    fallos_auth += 1
                    log.warning("auth del stream de datos rechazada (%s)", codigo_err)
                    if _anotar(almacen, {
                        "tipo": "auth_rechazada",
                        "recibido_en": _iso(ahora()),
                        "code": codigo_err,
                    }) == "disco":
                        return 1
                    if codigo_err == 409 or fallos_auth >= max_fallos_auth:
                        log.error("auth del stream de datos no se recupera (%s)", codigo_err)
                        return 1
                    codigo = "auth"
                elif estado_auth != "ok":
                    codigo = "parada" if estado_auth == "parada" else "caida"
                else:
                    universo = universo_fn()
                    await sock.enviar(json.dumps(mensaje_subscribe(universo.queries)))
                    estado_sub, msgs = await _hasta(
                        sock, parada, espera_auth,
                        lambda m: clasificar(m) == "subscription",
                    )
                    if estado_sub == "error":
                        codigo_err = codigo_error(msgs[-1]) if msgs else None
                        if codigo_err == 410 and universo.queries:
                            await sock.enviar(json.dumps(mensaje_subscribe(universo.queries, correcciones=False)))
                            estado_sub, msgs = await _hasta(
                                sock, parada, espera_auth,
                                lambda m: clasificar(m) == "subscription",
                            )
                        elif codigo_err == 405:
                            log.warning("tope de símbolos del plan; no se reintenta con más")
                            codigo = "limite_simbolos"
                            estado_sub = "error"
                        elif codigo_err == 406:
                            codigo = "limite_conexion"
                            estado_sub = "error"
                    if codigo in ("limite_simbolos", "limite_conexion"):
                        pass
                    elif estado_sub != "ok":
                        codigo = "parada" if estado_sub == "parada" else "caida"
                    else:
                        confirmados = None
                        for msg in msgs:
                            if clasificar(msg) == "subscription":
                                if cazar_wildcard(msg):
                                    codigo = "wildcard"
                                    confirmados = None
                                    break
                                confirmados = hunters_confirmados(msg, universo.mapa)
                        if codigo == "wildcard":
                            log.error("suscripción comodin; se cierra sin usarla")
                        else:
                            fallos_auth = 0
                            caidas = 0
                            almacen.marcar_conectado(ahora(), confirmados, universo, modo_efectivo)
                            log.info(
                                "escuchando barras SIP (%s símbolos, modo %s)",
                                almacen.estado.get("n_simbolos"), modo_efectivo,
                            )
                            queries = list(universo.queries)
                            codigo = await _consumir(
                                sock, almacen, vigilancia, parada, ahora, universo_fn,
                                modo_efectivo, espera_recepcion, queries,
                            )
            except ParadaPedida:
                codigo = "parada"
            except ConexionCerrada:
                codigo = "caida"
            except Exception as ex:
                log.warning("stream SIP caído (%s)", type(ex).__name__)
                codigo = "caida"
            finally:
                await _cerrar(sock)

            if codigo == "parada" or parada.is_set():
                try:
                    almacen.marcar_desconectado(ahora())
                except OSError:
                    return 1
                return 0
            if codigo == "disco":
                return 1
            if codigo == "wildcard":
                return 1
            if codigo == "auth":
                await dormir(espera_de_reintento(fallos_auth))
                continue
            if codigo == "limite_simbolos":
                try:
                    almacen.abrir_hueco(ahora(), "limite_simbolos")
                except OSError:
                    return 1
                await dormir(espera_de_reintento(1))
                continue
            caidas += 1
            motivo = "limite_conexion" if codigo == "limite_conexion" else "reconexion"
            try:
                almacen.abrir_hueco(ahora(), motivo)
            except OSError:
                return 1
            espera = espera_de_reintento(caidas)
            if codigo == "limite_conexion":
                # 406: ya hay otra conexión. Esperar más no abre una
                # segunda; el candado local igual impide el duplicado
                # en esta máquina.
                espera = max(espera, BACKOFF_TOPE_S)
            log.warning("reconexión SIP en %.1f s (caída %d, %s)", espera, caidas, motivo)
            if comparar_cada_s is not None:
                ultima_comparacion = await _quizas_comparar(
                    almacen, ahora(), ultima_comparacion, comparar_cada_s,
                )
            await dormir(espera)
    finally:
        almacen.cerrar()
    return 0


async def _quizas_comparar(almacen: Almacen, ahora: datetime, ultima: datetime | None, cada_s: float):
    if ultima is not None and (ahora - ultima).total_seconds() < cada_s:
        return ultima
    try:
        from momentum_hunter.data.sip_stream_comparar import comparar_almacen

        await asyncio.to_thread(comparar_almacen, almacen.raiz, ahora)
    except Exception as ex:
        log.warning("comparador SIP no corrió (%s)", type(ex).__name__)
    return ahora


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Stream SIP de barras de minuto. Un solo socket de datos; no coloca órdenes.",
    )
    ap.add_argument(
        "--directorio",
        default=None,
        help="almacén (default $MOMENTUM_ESTADO_DIR/sip_stream)",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    directorio = Path(args.directorio) if args.directorio else None
    try:
        return asyncio.run(correr(
            directorio=directorio,
            api_key=os.environ.get("ALPACA_PAPER_API_KEY"),
            api_secret=os.environ.get("ALPACA_PAPER_API_SECRET"),
            instalar_senales=True,
            comparar_cada_s=COMPARAR_CADA_S,
        ))
    except UrlNoEsSip:
        log.error("no se arranca: el stream no es el SIP de datos")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
