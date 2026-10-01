#!/usr/bin/env python3
"""Genera el panel del bot como una página HTML estática. Solo lectura.

Fuentes:
  - watchlist.json            salida del hunter (solo la marca de ruptura)
  - logs/events.jsonl         eventos escritos con dashboard.events.log_event
  - API de Alpaca PAPER       solo peticiones GET, endpoint fijo.
                              Posiciones, pendientes y cerradas hoy salen de aquí.
  - revisiones.json           solo el aviso de reconciliación, no qué se grafica
  - velas de 1 min            SIP en data.alpaca.markets; Yahoo solo de respaldo (dashboard/velas.py)
  - telemetría del hunter     la píldora de fuente de datos, si el campo existe

Regla del panel: un dato que falta se muestra como "—", nunca como 0.

Uso:  python -m dashboard.build_dashboard
"""
from __future__ import annotations

import html
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dashboard import gha as dg
from dashboard import velas as dv

# Catálogo de códigos de bloqueo del ejecutor (2026-09-23). Es un módulo
# de constantes sin dependencias; si no se puede importar (panel instalado
# sin el paper trader), el panel lo dice y trata todo código como nuevo:
# mejor un "Revisar" de más que un límite invisible.
try:
    from momentum_paper_trader import bloqueos as _catalogo_bloqueos
except Exception:  # pragma: no cover - sin paper trader instalado
    _catalogo_bloqueos = None

# La misma consulta y la misma noción de "orden viva" que la
# reconciliación. Si no se puede importar, el panel no afirma que falte
# el stop: un dato que no se pudo leer no es evidencia.
try:
    from momentum_paper_trader.alpaca_client import (
        orden_sigue_viva as _orden_sigue_viva,
        ordenes_con_patas as _ordenes_con_patas,
        parametros_ordenes_de_simbolos as _parametros_ordenes_de_simbolos,
    )
except Exception:  # pragma: no cover - sin paper trader instalado
    _orden_sigue_viva = None
    _ordenes_con_patas = None
    _parametros_ordenes_de_simbolos = None

# Misma cobertura que `reconciliacion.detectar`: whitelist
# held/new/accepted/pending_new. Sin esto el panel no decide si hay stop.
try:
    from momentum_paper_trader.reconciliacion import (
        cobertura as _cobertura,
        ventas_vivas as _ventas_vivas,
    )
except Exception:  # pragma: no cover - sin paper trader instalado
    _cobertura = None
    _ventas_vivas = None

# Los topes del ejecutor, para mostrar cuánto se usa de cada uno. Se leen
# de la MISMA config que aplica el ejecutor: si el panel los copiara, un
# cambio allá dejaría al panel mintiendo. Sin config, "sin dato".
try:
    from momentum_paper_trader.config import CONFIG as _CONFIG_PAPER
except Exception:  # pragma: no cover - sin paper trader instalado
    _CONFIG_PAPER = None

ALPACA_PAPER = "https://paper-api.alpaca.markets"  # fijo: el panel nunca habla con la cuenta real
REPO = Path(__file__).resolve().parents[1]
# Sin DASH_CACHE_VELAS la caché va al directorio temporal del sistema,
# NUNCA al árbol del repo: un archivo suelto dentro de /opt/hernan-portafolio
# rompe el `git pull --rebase` del wrapper (medido 2026-09-18).
CACHE_VELAS_DEFECTO = Path(tempfile.gettempdir()) / "momentum-dashboard-cache"
NY = ZoneInfo("America/New_York")


# ───────────────────────── configuración ─────────────────────────

def _env_float(nombre: str, defecto: float | None = None) -> float | None:
    valor = os.environ.get(nombre, "").strip()
    if not valor:
        return defecto
    try:
        return float(valor)
    except ValueError:
        return defecto


def _estado(relativo: str, *, es_dir: bool = False) -> Path:
    """Sin DASH_* el panel lee el mismo directorio que el bot
    (`MOMENTUM_ESTADO_DIR`), no el checkout."""
    from momentum_hunter.rutas_estado import resolver
    return resolver(relativo, es_dir=es_dir)


def cargar_config() -> dict:
    revisiones = os.environ.get("DASH_REVISIONES", "").strip()
    watchlist = os.environ.get("DASH_WATCHLIST", "").strip()
    telem = os.environ.get("DASH_TELEM_HUNTER", "").strip()
    return {
        # Canónico en el directorio de estado (el buscador lo escribe; el
        # ejecutor no). Overlay de runtime, también fuera de git.
        "watchlist": Path(watchlist) if watchlist else _estado("momentum_hunter/watchlist.json"),
        # Registro de auditoría del escaneo para noticias.html (solo lectura).
        "noticias_leidas": (Path(os.environ["DASH_NOTICIAS_LEIDAS"]) if os.environ.get("DASH_NOTICIAS_LEIDAS")
                            else _estado("momentum_hunter/noticias_leidas.json")),
        "watchlist_estado": Path(os.environ.get(
            "DASH_WATCHLIST_ESTADO",
            os.environ.get("MOMENTUM_WATCHLIST_STATE", "/var/lib/momentum/watchlist_vps_state.json"))),
        "eventos": Path(os.environ.get("DASH_EVENTOS", "logs/events.jsonl")),
        "salida": Path(os.environ.get("DASH_SALIDA", "dashboard_site")),
        "presupuesto_velas": _env_float("DASH_PRESUPUESTO_VELAS", 8.0),
        "hunter_max_min": _env_float("DASH_HUNTER_MAX_MIN", 45.0),
        "rechequeo_max_min": _env_float("DASH_RECHEQUEO_MAX_MIN", 12.0),
        # GitHub Actions (momentum_hunter.yml) sin correr en sesión más de
        # esto es alerta, aunque el VPS esté sano: el respaldo se cayó.
        "gha_max_min": _env_float("DASH_GHA_MAX_MIN", 45.0),
        "tz": ZoneInfo(os.environ.get("DASH_TZ", "UTC")),
        # Velas de los tickers en operación: caché fuera de git (en el VPS,
        # junto al HTML) y tope de tickers por corrida para no saturar a Yahoo.
        "cache_velas": Path(os.environ.get("DASH_CACHE_VELAS") or CACHE_VELAS_DEFECTO),
        "velas_ttl_seg": _env_float("DASH_VELAS_TTL_SEG", 120.0),
        "velas_max_tickers": int(_env_float("DASH_VELAS_MAX_TICKERS", 6.0) or 6),
        "velas_pausa_seg": _env_float("DASH_VELAS_PAUSA_SEG", 900.0),
        # Última corrida exitosa del hunter en GitHub Actions (dashboard/gha.py):
        # API pública, sin token, con caché en la misma carpeta que las velas.
        # Sin repo configurado el panel no pregunta y el Hunter queda "Sin datos".
        "gha_repo": os.environ.get("DASH_GHA_REPO", "h3rnan05/hernan-portafolio").strip(),
        "gha_workflow": os.environ.get("DASH_GHA_WORKFLOW", "momentum_hunter.yml").strip(),
        "gha_ttl_seg": _env_float("DASH_GHA_TTL_SEG", 300.0),
        # Escaneos del hunter en el VPS: telemetría JSONL por fuente
        # (momentum_hunter/telemetria/{fecha}/vps/events.jsonl).
        "telem_hunter": Path(telem) if telem else _estado("momentum_hunter/telemetria", es_dir=True),
        # Archivo de pausa del BOT ante un 429 de Yahoo (solo lectura).
        "pausa_bot": (Path(os.environ["DASH_YAHOO_PAUSA_BOT"]) if os.environ.get("DASH_YAHOO_PAUSA_BOT") else None),
        # Libro del ejecutor, SOLO para el aviso de reconciliación (¿el
        # broker tiene algo que nadie sigue?). No decide qué está abierto:
        # eso lo dice Alpaca. Ruta del paquete, no del cwd: el servicio y
        # un `python -m` lanzado desde otro directorio leen el mismo archivo
        # que escribe `estado.guardar`.
        "revisiones": Path(revisiones) if revisiones else _estado("momentum_paper_trader/revisiones.json"),
        # Aprendizaje en sombra (2026-10-01): reporte nocturno, ajustes,
        # régimen y sombra diaria. Solo lectura.
        "aprendizaje": (Path(os.environ["DASH_APRENDIZAJE"]) if os.environ.get("DASH_APRENDIZAJE")
                        else _estado("momentum_paper_trader/aprendizaje", es_dir=True)),
    }


# ───────────────────────── utilidades ─────────────────────────

def parse_ts(valor) -> datetime | None:
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        segundos = valor / 1000 if valor > 1e12 else valor
        try:
            return datetime.fromtimestamp(segundos, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(valor, str):
        texto = valor.strip().replace("Z", "+00:00")
        texto = re.sub(r"(\.\d{6})\d+", r"\1", texto)  # Alpaca manda nanosegundos
        try:
            d = datetime.fromisoformat(texto)
        except ValueError:
            return None
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return None


def num(valor) -> float | None:
    if valor is None or isinstance(valor, bool):
        return None
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _primero(d: dict, *claves):
    for c in claves:
        v = d.get(c)
        if v not in (None, "", []):
            return v
    return None


def inicio_dia_ny(ahora: datetime) -> datetime:
    return ahora.astimezone(NY).replace(hour=0, minute=0, second=0, microsecond=0)


def sesion_abierta(ahora: datetime) -> bool:
    """Sesión regular según el calendario local. Feriado o sin archivo: cerrada."""
    try:
        from momentum_hunter.calendario import consultar
    except Exception:
        return False
    consulta = consultar(ahora)
    if consulta.dia is None:
        return False
    ny = ahora.astimezone(NY)
    return consulta.dia.apertura <= ny.time() < consulta.dia.cierre


def percentil(valores: list[float], p: float) -> float | None:
    if not valores:
        return None
    ordenados = sorted(valores)
    k = max(0, math.ceil(p / 100 * len(ordenados)) - 1)
    return ordenados[k]


# ───────────────────────── fuentes ─────────────────────────

def leer_eventos(ruta: Path, desde: datetime):
    eventos, malas = [], 0
    try:
        with ruta.open(encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if not linea:
                    continue
                try:
                    e = json.loads(linea)
                except json.JSONDecodeError:
                    malas += 1
                    continue
                ts = parse_ts(e.get("ts")) if isinstance(e, dict) else None
                if ts is None:
                    malas += 1
                    continue
                if ts >= desde:
                    e["_ts"] = ts
                    eventos.append(e)
    except FileNotFoundError:
        return [], 0, f"No existe el log de eventos ({ruta})."
    except OSError as exc:
        return [], 0, f"No se pudo leer el log de eventos: {exc}"
    eventos.sort(key=lambda e: e["_ts"])
    return eventos, malas, None


CLAVES_LISTA = ("entradas", "watchlist", "tickers", "candidatos", "candidates", "items", "symbols")


def _cap(x: dict):
    if "es_large_cap" in x and isinstance(x["es_large_cap"], bool):
        return "large" if x["es_large_cap"] else "small"
    return _primero(x, "cap", "cap_class", "segmento", "universe")  # ausente = None, nunca "small"


def _catalizador(x: dict):
    tipo = _primero(x, "catalizador_tipo")
    titular = _primero(x, "catalizador_titular")
    if tipo or titular:
        return " · ".join(str(v) for v in (tipo, titular) if v)
    return _primero(x, "catalizador", "catalyst", "keyword", "keywords", "motivo")


def leer_estado_vps(ruta: Path):
    """Overlay que escribe el VPS (--solo-watchlist). Devuelve ({ticker: campos}, error).

    Si el archivo no existe no es error: el overlay puede estar apagado
    (MOMENTUM_WATCHLIST_VPS_STATE=0) y entonces manda el canónico.
    """
    try:
        crudo = json.loads(ruta.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, None
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"No se pudo leer el estado VPS de la watchlist ({type(exc).__name__})."
    entradas = crudo.get("entries") if isinstance(crudo, dict) else None
    if not isinstance(entradas, dict):
        return {}, "Estado VPS de la watchlist con formato no reconocido."
    return {str(k).upper(): v for k, v in entradas.items() if isinstance(v, dict)}, None


def _estados_fusionados(lista: list, ruta_estado: Path | None):
    """Estado y `actualizado_en` de cada entrada tras aplicar el overlay VPS
    con las MISMAS reglas que usa el ejecutor (`watchlist.aplicar_overlay`,
    vía `cargar_con_overlay`). Devuelve
    ({(ticker, creado_en): (estado, actualizado_en)}, error).

    Antes el panel ponía el estado del overlay encima del canónico sin más
    reglas: con el rechequeo del VPS apagado, el overlay se quedaba viejo y
    mostraba "watching" para una entrada que GHA ya había expirado (SUNB,
    2026-09-11). Con las reglas de `watchlist.py` el canónico terminal gana
    y un overlay más viejo que el canónico no pisa nada.

    Clave (ticker, creado_en): un ticker puede aparecer dos veces (la
    EXPIRED vieja y el intento nuevo). Lo que no se pueda fusionar (formato
    ajeno, entrada incompleta) se queda con el canónico."""
    if ruta_estado is None:
        return {}, None
    _, err = leer_estado_vps(ruta_estado)
    if err:
        return {}, err
    try:
        from momentum_hunter import watchlist as wl
    except ImportError as exc:
        return {}, (f"No se pudo cargar momentum_hunter.watchlist ({type(exc).__name__}); "
                    "se muestra el canónico sin el estado VPS.")
    crudas = [{"nombre": None, **x} for x in lista if isinstance(x, dict)]
    entradas = wl.aplicar_overlay(wl.parsear({"entradas": crudas}), ruta_estado)
    return {(e.ticker, e.creado_en): (e.estado, e.actualizado_en) for e in entradas}, None


def fecha_ultimo_commit(ruta: Path) -> datetime | None:
    """Fecha del último commit que tocó `ruta` (`git log -1 --format=%cI`).
    None si no es un repo git, git no está o falla por lo que sea. En un clon
    superficial (`--depth`) el historial está cortado y git atribuiría el
    archivo al commit más viejo que tiene, así que ahí tampoco hay dato."""
    try:
        sup = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=ruta.parent, capture_output=True, text=True, timeout=10,
        )
        if sup.returncode != 0 or sup.stdout.strip() != "false":
            return None
        r = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--", ruta.name],
            cwd=ruta.parent, capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return parse_ts(r.stdout.strip())


# Marcas que escribe el hunter dentro de cada entrada (no el overlay VPS).
CLAVES_TS_ENTRADA = ("actualizado_en", "watchlist_escrito_ts", "creado_en",
                     "updated_at", "detectado", "detected_at", "added_at", "timestamp", "ts")


def leer_watchlist(ruta: Path, ruta_estado: Path | None = None):
    """Devuelve (items, momento_generado, error).

    `momento_generado` sale del propio JSON (marca de nivel superior o la
    entrada más reciente) o, si no hay, del último commit que tocó el
    archivo. Nunca del mtime: tras un `git clone`/`git pull` el mtime es la
    hora de la descarga, no la del hunter, y daría un "OK" falso. Si no hay
    ninguna marca, None ("Sin datos")."""
    try:
        crudo = json.loads(ruta.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], None, f"No existe {ruta}."
    except (OSError, json.JSONDecodeError) as exc:
        return [], None, f"No se pudo leer la watchlist: {exc}"

    generado, lista = None, crudo
    if isinstance(crudo, dict):
        generado = parse_ts(_primero(crudo, "generado", "generated_at", "updated_at", "ts"))
        lista = next((crudo[c] for c in CLAVES_LISTA if isinstance(crudo.get(c), list)), None)
    if not isinstance(lista, list):
        return [], None, "Formato de watchlist no reconocido: ajusta leer_watchlist()."

    fusionados, err_estado = _estados_fusionados(lista, ruta_estado)

    items = []
    mas_reciente = None
    for x in lista:
        if isinstance(x, str):
            x = {"ticker": x}
        if not isinstance(x, dict):
            continue
        for clave in CLAVES_TS_ENTRADA:
            ts = parse_ts(x.get(clave))
            if ts is not None and (mas_reciente is None or ts > mas_reciente):
                mas_reciente = ts
        catalizador = _catalizador(x)
        if isinstance(catalizador, list):
            catalizador = ", ".join(map(str, catalizador))
        ticker = _primero(x, "ticker", "symbol", "simbolo")
        # Estado y fecha ya fusionados con las reglas de `watchlist.py`; sin
        # fusión (formato ajeno, sin overlay) manda el canónico.
        estado, actualizado = fusionados.get(
            (ticker, x.get("creado_en")),
            (_primero(x, "estado", "status", "state"), _primero(x, "actualizado_en", "updated_at")))
        items.append({
            "ticker": ticker,
            "cap": _cap(x),
            "catalizador": catalizador,
            "detectado": parse_ts(_primero(x, "detectado", "detected_at", "creado_en", "timestamp", "added_at", "ts")),
            "estado": estado,
            "actualizado": parse_ts(actualizado),
            # Nivel de ruptura que calculó el hunter ("la entrada que se
            # esperaba"). Ausente = None: el panel dice "sin dato".
            "ruptura": num(x.get("ultima_zona_entrada_baja")),
            "creado_en": parse_ts(x.get("creado_en")),
        })
    if generado is None:
        candidatos = [t for t in (mas_reciente, fecha_ultimo_commit(ruta)) if t is not None]
        generado = max(candidatos) if candidatos else None
    return items, generado, err_estado


def _contar_trading() -> None:
    """El panel también gasta cupo del límite de 200/min de trading
    (corre cada minuto). Si `uso_api` no se puede importar, el panel
    sigue igual: medir nunca rompe la página."""
    try:
        from uso_api.contador import TRADING, registrar
    except ImportError:
        return
    registrar(TRADING)


def alpaca_get(ruta: str, params: dict | None = None, timeout: float = 10):
    # Mismos nombres que usa momentum_paper_trader/run.py; APCA_* queda como respaldo.
    clave = os.environ.get("ALPACA_PAPER_API_KEY") or os.environ.get("APCA_API_KEY_ID")
    secreto = os.environ.get("ALPACA_PAPER_API_SECRET") or os.environ.get("APCA_API_SECRET_KEY")
    if not clave or not secreto:
        return None, "Faltan ALPACA_PAPER_API_KEY / ALPACA_PAPER_API_SECRET."
    url = ALPACA_PAPER + ruta
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method="GET", headers={
        "APCA-API-KEY-ID": clave,
        "APCA-API-SECRET-KEY": secreto,
    })
    _contar_trading()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8")), None
    except urllib.error.HTTPError as exc:
        return None, f"Alpaca respondió HTTP {exc.code} en {ruta}."
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return None, f"Alpaca no respondió en {ruta}: {exc}"


# ───────────────────────── cálculos ─────────────────────────

MEDIDA_LATENCIA = "ruptura_a_orden"


def _es_cupo_lleno(evento: dict) -> bool:
    if evento.get("tipo") not in ("capacidad_llena", "bloqueo_riesgo"):
        return False
    return _codigo_de(evento) == "MAXIMO_POSICIONES"


def intervalos_cupo_lleno(eventos: list[dict]) -> list[tuple[datetime, datetime]]:
    """Tramos en que el tope de posiciones estuvo lleno, armados con los
    eventos de cupo lleno (uno por corrida del vigía). Dos eventos a
    menos de `_HUECO_MAX_CICLO_SEG` son el mismo tramo; cada tramo dura
    hasta su último evento más un ciclo nominal. Sin eventos, ningún
    tramo: no se supone que hubo espera."""
    marcas = sorted(e["_ts"] for e in eventos if e.get("_ts") is not None and _es_cupo_lleno(e))
    tramos: list[list[datetime]] = []
    for m in marcas:
        if tramos and (m - tramos[-1][1]).total_seconds() <= _HUECO_MAX_CICLO_SEG:
            tramos[-1][1] = m
        else:
            tramos.append([m, m])
    return [(a, b + _CICLO_NOMINAL) for a, b in tramos]


def desglose_latencia(eventos: list[dict]) -> list[dict]:
    """Por cada orden con latencia completa (ruptura → orden): cuánto de
    ese tiempo fue esperar cupo y cuánto fue el bot reaccionando.

    RCL (29/9): señal de las 10:16, compra a las 12:41 porque CCL cerró en
    objetivo y liberó cupo. Esos ~90 min contaban como "llegó tarde", pero
    el bot no tardó: el tope de posiciones no lo dejaba entrar. La espera
    es la parte de [ruptura, orden] que cae en un tramo de cupo lleno; la
    reacción es el resto. Sin eventos de cupo, toda la latencia es
    reacción (el lado prudente: no se esconde una tardanza).

    Solo cuentan las órdenes con `velas` y `medida == "ruptura_a_orden"`:
    si el hunter no guardó las velas previas al disparo, no se reconstruye
    por tiempo (eso mediría otra cosa); va a `compras_sin_latencia`."""
    tramos = intervalos_cupo_lleno(eventos)
    salida = []
    for e in eventos:
        if e.get("tipo") != "orden" or e.get("estado") != "enviada" or not e.get("ticker"):
            continue
        if e.get("medida") != MEDIDA_LATENCIA:
            continue
        total = num(e.get("velas"))
        if total is None or total < 0 or e.get("_ts") is None:
            continue
        fin = e["_ts"]
        ini = fin - timedelta(minutes=total)
        espera_s = sum(max(0.0, (min(fin, b) - max(ini, a)).total_seconds()) for a, b in tramos)
        espera = min(total, round(espera_s / 60, 1))
        salida.append({"ticker": str(e["ticker"]), "hora": fin, "total": round(total, 1),
                       "espera": espera, "reaccion": round(total - espera, 1)})
    return salida


def compras_sin_latencia(eventos: list[dict], ordenes_hoy: list | None, hay_eventos: bool) -> list[dict]:
    """Compras de hoy que no pueden tener barra, y por qué. Antes se
    perdían sin aviso: 6 compras y 5 barras (29/9)."""
    salida = []
    con_evento = set()
    for e in eventos:
        if e.get("tipo") != "orden" or e.get("estado") != "enviada" or not e.get("ticker"):
            continue
        con_evento.add(str(e["ticker"]))
        if e.get("medida") == MEDIDA_LATENCIA and num(e.get("velas")) is not None:
            continue
        if e.get("medida") != MEDIDA_LATENCIA:
            motivo = "el evento no trae la medida ruptura → orden"
        elif e.get("velas_desde_ruptura") is None:
            motivo = "el hunter no contó las velas desde la ruptura"
        elif e.get("velas_desde_disparo") is None:
            motivo = "falta la hora de la vela que disparó"
        else:
            motivo = "latencia sin dato"
        salida.append({"ticker": str(e["ticker"]), "motivo": motivo})
    # Una compra que Alpaca tiene y el log no: el evento no se escribió
    # (log_event se traga los fallos para no tocar la orden). Solo se
    # afirma si el log existe; sin log ya hay un aviso aparte.
    if hay_eventos and isinstance(ordenes_hoy, list):
        vistos = set()
        for o in ordenes_hoy:
            if not isinstance(o, dict) or str(o.get("side") or "").lower() != "buy":
                continue
            simbolo = o.get("symbol")
            if not isinstance(simbolo, str) or not simbolo or simbolo in con_evento or simbolo in vistos:
                continue
            vistos.add(simbolo)
            salida.append({"ticker": simbolo, "motivo": "compra en Alpaca sin evento en el log"})
    return salida


def _edad_min(ahora: datetime, momento: datetime | None) -> float | None:
    return None if momento is None else (ahora - momento).total_seconds() / 60


def _estado_frescura(edad: float | None, maximo: float | None, en_sesion: bool) -> str:
    if edad is None:
        return "sin-datos"
    if en_sesion and maximo is not None and edad > maximo:
        return "alerta"
    return "ok"


def _codigo_de(evento: dict) -> str:
    if _catalogo_bloqueos is not None:
        return _catalogo_bloqueos.codigo_de_evento(evento)
    codigo = evento.get("codigo") or evento.get("limite") or "SIN_CODIGO"
    return str(codigo).upper()


def _es_dato_faltante(codigo: str) -> bool:
    if _catalogo_bloqueos is not None:
        return _catalogo_bloqueos.es_dato_faltante(codigo)
    return codigo.startswith("DATO_FALTANTE:")


def _es_mercado_cerrado(codigo: str) -> bool:
    """El mercado cerrado no es un límite lleno ni un fallo de riesgo:
    es el ejecutor negándose a encolar una orden para mañana."""
    if _catalogo_bloqueos is not None:
        return codigo == _catalogo_bloqueos.MERCADO_CERRADO
    return codigo == "MERCADO_CERRADO"


def _ticker_de(evento: dict) -> str:
    """Ticker del evento. `creado_en` no entra: dos TRIGGERED del mismo
    símbolo (la sombra y la viva, o un reingreso) son la misma fila."""
    bruto = evento.get("ticker")
    if bruto in (None, ""):
        bruto = evento.get("symbol")
    texto = str(bruto).strip() if bruto not in (None, "") else ""
    return texto or "—"


# El vigía dispara un rechequeo cada 60 s. "Activo ahora" son los últimos
# tres de esos ciclos. Sin ninguna marca no hay cadencia que medir y la
# ventana cae a esos mismos ~3 minutos: un bloqueo de la mañana no puede
# seguir pintando la tarjeta en rojo por la tarde.
_CICLOS_ACTIVOS = 3
_CICLO_NOMINAL = timedelta(seconds=60)
# Un hueco mayor que esto es una pausa (cierre, caída), no la cadencia.
_HUECO_MAX_CICLO_SEG = 300.0


def _marcas_rechequeo(rechequeos: list[dict]) -> list[datetime]:
    return sorted(e["_ts"] for e in rechequeos if e.get("_ts") is not None)


def _duracion_ciclo(marcas: list[datetime]) -> timedelta:
    if len(marcas) < 2:
        return _CICLO_NOMINAL
    huecos = [(b - a).total_seconds() for a, b in zip(marcas, marcas[1:])]
    plausibles = [h for h in huecos if 1 <= h <= _HUECO_MAX_CICLO_SEG]
    if not plausibles:
        return _CICLO_NOMINAL
    return timedelta(seconds=statistics.median(plausibles[-_CICLOS_ACTIVOS:]))


def _inicio_ventana_activa(rechequeos: list[dict], ahora: datetime) -> datetime:
    """Inicio de lo que sigue pasando ahora.

    Con rechequeos, el borde es la marca que abre el más antiguo de los
    últimos tres ciclos. Si la última marca ya quedó detrás de esa
    ventana (el vigía no está ciclando), se ancla a ahora − 3 ciclos:
    los ciclos de la mañana no cuentan como activos. Sin ninguna marca,
    los últimos ~3 minutos.
    """
    marcas = _marcas_rechequeo(rechequeos)
    ciclo = _duracion_ciclo(marcas)
    ancla = ahora - _CICLOS_ACTIVOS * ciclo
    if not marcas:
        return ancla
    if marcas[-1] < ancla:
        return ancla
    if len(marcas) >= _CICLOS_ACTIVOS:
        return marcas[-_CICLOS_ACTIVOS]
    return marcas[0]


def _cubeta_de(ts: datetime, marcas: list[datetime], ciclo: timedelta):
    """Identidad del ciclo al que pertenece `ts`.

    Dos eventos del mismo ticker y código dentro del mismo ciclo (las
    dos TRIGGERED con distinto `creado_en`) cuentan una vez. Sin una
    marca de rechequeo previa, el ciclo es el minuto nominal: ticks a
    un minuto de distancia siguen siendo corridas distintas.
    """
    previas = [m for m in marcas if m <= ts]
    if previas:
        return previas[-1]
    ancho = ciclo.total_seconds()
    if ancho <= 0:
        return ts
    return int(ts.timestamp() // ancho)


def resumir_bloqueos(bloqueos: list[dict], capacidad: list[dict], tz, ahora: datetime,
                     rechequeos: list[dict] | None = None) -> dict:
    """Bloqueos únicos por (ticker, código) y capacidad por código.

    `revisar` mira SOLO lo que cayó en la ventana activa (últimos 3
    ciclos del vigía). El resto del día es historial: se conserva con
    desde/hasta y veces, pero no pinta la tarjeta.

    Por qué (2026-09-23, y el rojo eterno del 2026-09-28): 942 eventos
    crudos eran un tope repetido, y 6468 `DATO_FALTANTE` que pararon a
    media tarde seguían en rojo porque el veredicto sumaba el día
    entero. Una fila es (ticker, código): el `creado_en` de una segunda
    TRIGGERED del mismo símbolo no abre otra fila.
    """
    conocidos = set(_catalogo_bloqueos.CODIGOS_CONOCIDOS) if _catalogo_bloqueos is not None else set()
    marcas = _marcas_rechequeo(rechequeos or [])
    ciclo = _duracion_ciclo(marcas)
    inicio = _inicio_ventana_activa(rechequeos or [], ahora)

    unicos: dict[tuple[str, str], dict] = {}
    for b in bloqueos:
        codigo = _codigo_de(b)
        clave = (_ticker_de(b), codigo)
        fila = unicos.setdefault(clave, {
            "ticker": clave[0], "codigo": codigo, "veces": 0, "ultimo": None, "primero": None,
            "motivo": str(b.get("motivo") or ""), "_cubetas": set(),
        })
        cubeta = _cubeta_de(b["_ts"], marcas, ciclo)
        if cubeta not in fila["_cubetas"]:
            fila["_cubetas"].add(cubeta)
            fila["veces"] += 1
        if fila["primero"] is None or b["_ts"] < fila["primero"]:
            fila["primero"] = b["_ts"]
        if fila["ultimo"] is None or b["_ts"] > fila["ultimo"]:
            fila["ultimo"] = b["_ts"]
    filas = sorted(unicos.values(), key=lambda f: (-f["veces"], f["ticker"]))
    for f in filas:
        f.pop("_cubetas", None)
        f["hora"] = _hora(f["ultimo"], tz, ahora=ahora)
        f["desde"] = _hora(f["primero"], tz, ahora=ahora)
        f["hasta"] = _hora(f["ultimo"], tz, ahora=ahora)
        # Activo solo si la ÚLTIMA vez cae en la ventana. Si paró antes,
        # ya se resolvió aunque se haya repetido miles de veces.
        f["activo"] = f["ultimo"] is not None and f["ultimo"] >= inicio

    por_codigo_cap: dict[str, dict] = {}
    for c in capacidad:
        codigo = _codigo_de(c)
        fila = por_codigo_cap.setdefault(codigo, {
            "codigo": codigo, "corridas": 0, "primero": None, "ultimo": None,
            "motivo": str(c.get("motivo") or ""),
        })
        fila["corridas"] += 1
        if fila["primero"] is None or c["_ts"] < fila["primero"]:
            fila["primero"] = c["_ts"]
        if fila["ultimo"] is None or c["_ts"] > fila["ultimo"]:
            fila["ultimo"] = c["_ts"]
    capacidad_todas = sorted(por_codigo_cap.values(), key=lambda f: -f["corridas"])
    informativos: list[dict] = []
    capacidad_filas: list[dict] = []
    for f in capacidad_todas:
        f["desde"] = _hora(f["primero"], tz, ahora=ahora)
        f["hasta"] = _hora(f["ultimo"], tz, ahora=ahora)
        f["activo"] = f["ultimo"] is not None and f["ultimo"] >= inicio
        # MERCADO_CERRADO no es capacidad llena: informativo, aparte.
        if _es_mercado_cerrado(f["codigo"]):
            informativos.append(f)
        else:
            capacidad_filas.append(f)

    codigos_activos = {f["codigo"] for f in filas if f["activo"]}
    codigos_activos |= {f["codigo"] for f in capacidad_filas if f["activo"]}
    dato_faltante = sorted(c for c in codigos_activos if _es_dato_faltante(c))
    nuevos = sorted(
        c for c in codigos_activos
        if c not in conocidos and not _es_dato_faltante(c) and not _es_mercado_cerrado(c)
    )
    return {
        "eventos": sum(f["veces"] for f in filas),
        "unicos": filas,
        "capacidad": capacidad_filas,
        "informativos": informativos,
        "dato_faltante": dato_faltante,
        "codigos_nuevos": nuevos,
        "revisar": bool(dato_faltante or nuevos),
        "sin_catalogo": _catalogo_bloqueos is None,
        "inicio_activos": inicio,
    }


def resumir_stop_diario(eventos: list[dict], tz, ahora: datetime) -> dict:
    """Última medición del stop de pérdida diaria de la sesión de hoy
    (eventos `stop_diario` del ejecutor, 2026-10-01). Sin medición de
    hoy no se inventa un estado: `sin_datos`."""
    hoy = ahora.astimezone(NY).date().isoformat()
    ultimos = [e for e in eventos
               if e.get("tipo") == "stop_diario" and e.get("_ts") is not None
               and str(e.get("fecha_sesion") or "") == hoy]
    if not ultimos:
        return {"sin_datos": True}
    e = max(ultimos, key=lambda x: x["_ts"])
    activado = None
    if e.get("activado_en"):
        try:
            activado = datetime.fromisoformat(str(e["activado_en"]).replace("Z", "+00:00"))
        except ValueError:
            activado = None
    codigo = e.get("codigo")
    return {
        "sin_datos": False,
        "modo": e.get("modo"),
        "pct": _num(e.get("pct")),
        "pnl": _num(e.get("pnl")),
        "pnl_pct": _num(e.get("pnl_pct")),
        "umbral_usd": _num(e.get("umbral_usd")),
        "activo": e.get("activo") is True,
        "bloquea": e.get("bloquea") is True,
        "codigo": codigo,
        "dato_faltante": _es_dato_faltante(codigo) if codigo else False,
        "motivo": str(e.get("motivo") or ""),
        "desde": _hora(activado, tz, ahora=ahora) if activado else None,
        "ultimo": _hora(e["_ts"], tz, ahora=ahora),
        "ultimo_ts": e["_ts"],
    }


def _num(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _frase_intervalo(fila: dict, n: int, unidad: str) -> str:
    return f"{fila['codigo']} desde {fila['desde']} hasta {fila['hasta']} ({n} {unidad})"


def _detalle_riesgo(riesgo: dict, hay_eventos: bool, conteos_validos: bool, en_sesion: bool) -> str:
    """Una línea para la tarjeta. El color lo decide el llamador con el
    conjunto activo; aquí solo se nombra. Sin ciclo reciente en sesión
    (o sin log) no se finge un conteo vigente: "sin datos recientes".
    """
    sin_recientes = (not hay_eventos) or (en_sesion and not conteos_validos)
    if sin_recientes and not riesgo["revisar"]:
        return "— sin datos recientes"
    if not (riesgo["unicos"] or riesgo["capacidad"] or riesgo["informativos"] or conteos_validos):
        return "— bloqueos hoy"
    partes = [f"{len(riesgo['unicos'])} bloqueos únicos ({riesgo['eventos']} eventos) hoy"]
    for c in riesgo["capacidad"]:
        frase = _frase_intervalo(c, c["corridas"], "corridas")
        # "capacidad llena" en presente solo si la última corrida sigue
        # dentro de la ventana. Si no, es historial y lleva el hasta.
        if c.get("codigo") == "PERDIDA_DIARIA":
            # Stop de pérdida diaria (2026-10-01): no es un cupo, es la
            # regla de cuenta que corta entradas hasta la próxima sesión.
            partes.append(("stop diario activo: " if c.get("activo") else "historial: ") + frase
                          + (f" — {c['motivo']}" if c.get("activo") and c.get("motivo") else ""))
            continue
        partes.append(("capacidad llena: " if c.get("activo") else "historial: ") + frase)
    for c in riesgo["informativos"]:
        partes.append(
            f"mercado cerrado desde {c['desde']} hasta {c['hasta']} ({c['corridas']} corridas)"
        )
    if riesgo["dato_faltante"]:
        partes.append("revisar: " + ", ".join(riesgo["dato_faltante"]))
    if riesgo["codigos_nuevos"]:
        partes.append("motivo nuevo: " + ", ".join(riesgo["codigos_nuevos"]))
    return " · ".join(partes)


def _estado_riesgo(riesgo: dict, sin_datos_recientes: bool, conteos_validos: bool) -> str:
    """Color de la tarjeta. `revisar` ya está filtrado a la ventana activa.

    Orden: un dato faltante o un código nuevo activo manda (rojo). Si no
    se puede saber qué pasa ahora, no se pinta verde. Mercado cerrado
    como único hecho activo es neutro. Un límite conocido haciendo su
    trabajo, o un historial ya resuelto con el vigía fresco, es OK.
    """
    if riesgo["revisar"]:
        return "alerta"
    if sin_datos_recientes:
        return "sin-datos"
    activos = any(f.get("activo") for f in riesgo["unicos"]) or any(
        c.get("activo") for c in riesgo["capacidad"])
    mercado_activo = any(c.get("activo") for c in riesgo["informativos"])
    if mercado_activo and not activos:
        return "info"
    if conteos_validos or riesgo["unicos"] or riesgo["capacidad"] or riesgo["informativos"]:
        return "ok"
    return "sin-datos"


ESTADOS_ORDEN = {
    "filled": "ejecutada", "partially_filled": "parcial", "rejected": "rechazada",
    "canceled": "cancelada", "expired": "expirada", "new": "abierta", "accepted": "abierta",
    "pending_new": "abierta", "held": "en espera",
}


ESTADOS_ACTIVOS = ("watching", "triggered")


def filtrar_watchlist(items: list[dict], desde: datetime) -> list[dict]:
    """Activas siempre; terminales solo si cambiaron hoy. Activas primero, luego lo más reciente."""
    def activo(w):
        return str(w.get("estado") or "").lower() in ESTADOS_ACTIVOS

    visibles = [w for w in items if activo(w) or (w.get("actualizado") and w["actualizado"] >= desde)]
    minimo = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(visibles, key=lambda w: (not activo(w), -(w.get("actualizado") or minimo).timestamp()))


def uso_de_limites(posiciones, abiertas, filas_pos, equity, efectivo, config) -> dict:
    """Cuánto se usa AHORA de cada tope determinista del ejecutor.

    Cupo: el ejecutor cuenta tickers comprometidos = símbolos con
    posición ∪ símbolos con cualquier orden abierta
    (`executor._leer_cuenta`). Se cuenta igual, para que el panel diga
    lo mismo que el ejecutor va a decidir. Concentración: valor de
    mercado de la posición contra el equity, el mismo denominador que
    `maximo_pct_efectivo_por_posicion`. Un dato que falta deja su parte
    en None; nunca se cuenta como 0."""
    tope_cupo = getattr(config, "maximo_posiciones_abiertas", None) if config is not None else None
    tope_conc = getattr(config, "maximo_pct_efectivo_por_posicion", None) if config is not None else None
    comprometidos = None
    if posiciones is not None and abiertas is not None:
        simbolos = {p.get("symbol") for p in posiciones if isinstance(p, dict)}
        simbolos |= {o.get("symbol") for o in abiertas if isinstance(o, dict)}
        simbolos.discard(None)
        comprometidos = sorted(simbolos)
    concentracion = []
    for f in filas_pos or []:
        pct = (f["valor"] / equity * 100) if f.get("valor") is not None and equity else None
        concentracion.append({"ticker": f["ticker"], "pct": pct})
    riesgos = [f.get("riesgo") for f in filas_pos or []]
    riesgo_total = (round(sum(r for r in riesgos if r > 0), 2)
                    if filas_pos is not None and riesgos and all(r is not None for r in riesgos) else
                    (0.0 if filas_pos == [] else None))
    valores = [f.get("valor") for f in filas_pos or []]
    expuesto = (sum(valores) if filas_pos is not None and all(v is not None for v in valores) else None)
    return {
        "comprometidos": comprometidos,
        "tope_cupo": tope_cupo,
        "concentracion": concentracion,
        "tope_concentracion_pct": None if tope_conc is None else tope_conc * 100,
        "riesgo_total": riesgo_total,
        "riesgo_total_pct": (riesgo_total / equity * 100) if riesgo_total is not None and equity else None,
        "expuesto": expuesto,
        "expuesto_pct": (expuesto / equity * 100) if expuesto is not None and equity else None,
        "efectivo": efectivo,
    }


def construir(ahora: datetime, cfg: dict, get=alpaca_get, velas=None, gha=None) -> dict:
    desde = inicio_dia_ny(ahora)
    en_sesion = sesion_abierta(ahora)
    problemas = []

    eventos, malas, err = leer_eventos(cfg["eventos"], desde)
    # Sin log de eventos no se sabe cuántas decisiones o bloqueos hubo: eso
    # es "—", no 0. Un archivo que existe pero no tiene eventos hoy sí es 0.
    hay_eventos = err is None
    if err:
        problemas.append(err)
    if malas:
        problemas.append(f"{malas} líneas del log de eventos no se pudieron leer.")

    watch_todas, wl_momento, err = leer_watchlist(cfg["watchlist"], cfg.get("watchlist_estado"))
    if err:
        problemas.append(err)
    watch = filtrar_watchlist(watch_todas, desde)

    cuenta, err_c = get("/v2/account")
    posiciones, err_p = get("/v2/positions")
    ordenes, err_o = get("/v2/orders", {
        "status": "all", "limit": 500, "direction": "desc", "nested": "true",
        "after": desde.astimezone(timezone.utc).isoformat(),
    })
    # Compras de entrada todavía sin llenar: tabla de pendientes y gráfico.
    # El stop de una posición abierta no sale de aquí. Con el padre ya
    # filled, esta lista trae el take-profit y `legs` vacío; el stop
    # `held` cuelga de ese padre y lo pide `leer_ordenes_de_simbolos`.
    abiertas, err_a = get("/v2/orders", {"status": "open", "limit": 500, "nested": "true"})
    # Cerradas sin filtro de fecha: el fill de entrada de lo que se cerró
    # hoy puede ser de otro día. Sin ese precio el P&L realizado queda "—".
    # 500 es el tope de Alpaca; si la entrada queda más atrás, no se inventa.
    historicas, err_h = get("/v2/orders", {
        "status": "closed", "limit": 500, "direction": "desc", "nested": "true",
    })
    for e in (err_c, err_p, err_o, err_a, err_h):
        if e and e not in problemas:
            problemas.append(e)
    alpaca_ok = not (err_c or err_p or err_o)

    # Curva de equity: dos vistas del mismo endpoint (solo GET). Un error
    # de Alpaca va a "problemas"; un historial vacío o relleno con ceros
    # es "Sin datos" en el gráfico, nunca una línea en cero.
    equity_dia = _historial_equity(get, {"period": "1D", "timeframe": "5Min"}, problemas, ahora)
    equity_mes = _historial_equity(get, {"period": "1M", "timeframe": "1D"}, problemas, ahora)

    equity = num(cuenta.get("equity")) if isinstance(cuenta, dict) else None
    last_equity = num(cuenta.get("last_equity")) if isinstance(cuenta, dict) else None
    equity_dia = con_punto_en_vivo(equity_dia, equity, ahora)
    # Número de cuenta paper (no es una credencial: es el identificador que
    # Alpaca muestra en su propio panel). Se enseña para poder comprobar a
    # simple vista que el panel mira LA MISMA cuenta que el ejecutor.
    cuenta_numero = str(cuenta.get("account_number")).strip() if isinstance(cuenta, dict) and cuenta.get("account_number") else None
    pnl = equity - last_equity if equity is not None and last_equity is not None else None
    pnl_pct = pnl / last_equity * 100 if pnl is not None and last_equity else None

    n_pos = len(posiciones) if isinstance(posiciones, list) else None
    lista_ordenes = ordenes if isinstance(ordenes, list) else None
    n_ord = len(lista_ordenes) if lista_ordenes is not None else None
    n_rech = sum(1 for o in lista_ordenes if o.get("status") == "rejected") if lista_ordenes is not None else None

    # "Llegaron tarde" y las cifras cuentan la REACCIÓN del bot, no la
    # espera por cupo lleno (ver `desglose_latencia`).
    lat_detalle = desglose_latencia(eventos)
    lat = [(d["ticker"], d["reaccion"]) for d in lat_detalle]
    valores = [v for _, v in lat]
    presupuesto = cfg["presupuesto_velas"]

    rechequeos = [e for e in eventos if e.get("tipo") == "rechequeo"]
    decisiones = [e for e in eventos if e.get("tipo") == "decision"]
    bloqueos = [e for e in eventos if e.get("tipo") == "bloqueo_riesgo"]
    # El VPS registra `persist_fallido` cuando no consigue subir su estado
    # (revisiones, archivo, telemetría paper) a main. Mientras eso pase,
    # GitHub y el panel de GHA miran datos viejos: se pinta en rojo.
    persist_fallidos = [
        {"hora": _hora(e["_ts"], cfg["tz"], ahora=ahora),
         "motivo": str(e.get("motivo") or "sin motivo registrado"),
         "intentos": e.get("intentos")}
        for e in eventos if e.get("tipo") == "persist_fallido"
    ]
    # La IA no pudo decidir (saldo, clave, API, veredicto ilegible) y el
    # ejecutor dejó la señal TRIGGERED. No es un persist fallido: el git
    # puede estar sano y aun así nadie se entera de que no se opera.
    ia_fallos = [
        {"hora": _hora(e["_ts"], cfg["tz"], ahora=ahora),
         "codigo": str(e.get("codigo") or "api"),
         "motivo": str(e.get("motivo") or "fallo técnico de la IA"),
         "consecutivos": e.get("consecutivos")}
        for e in eventos if e.get("tipo") == "ia_fallo_tecnico"
    ]

    ult_rechequeo = rechequeos[-1]["_ts"] if rechequeos else None
    ult_decision = decisiones[-1]["_ts"] if decisiones else None

    por_limite: dict[str, int] = {}
    for b in bloqueos:
        nombre = str(b.get("limite") or "sin nombre")
        por_limite[nombre] = por_limite.get(nombre, 0) + 1

    # Bloqueos ÚNICOS (2026-09-23): el mismo ticker bloqueado por el mismo
    # motivo en 120 ticks es UN hecho repetido, no 120. Se cuenta por
    # (ticker, código) con veces y última hora. "Revisar" solo si hay un
    # bloqueo por DATO faltante/nulo/viejo (`DATO_FALTANTE:<campo>`) o un
    # código que el catálogo no conoce; un límite conocido haciendo su
    # trabajo es "OK", por muchas veces que se repita.
    riesgo = resumir_bloqueos(
        bloqueos, [e for e in eventos if e.get("tipo") == "capacidad_llena"],
        cfg["tz"], ahora, rechequeos=rechequeos,
    )
    if riesgo["sin_catalogo"]:
        problemas.append("No se pudo cargar el catálogo de códigos de bloqueo (momentum_paper_trader.bloqueos): "
                         "todo código se trata como nuevo.")

    estado_rechequeo = _estado_frescura(_edad_min(ahora, ult_rechequeo), cfg["rechequeo_max_min"], en_sesion)
    # Un 0 solo es un dato si hay log Y el bot corrió hace poco. Sin rechequeo
    # reciente, "0 decisiones" o "0 bloqueos" no significa que no haya pasado nada.
    conteos_validos = hay_eventos and estado_rechequeo == "ok"
    motivo_sin_datos = ("no hay log de eventos" if not hay_eventos
                        else "no hay un rechequeo reciente")
    # Fail-closed de la tarjeta: sin log, o sesión abierta y ni un ciclo
    # reciente, no hay con qué decir que "ahora" está limpio. No es verde.
    sin_datos_recientes = (not hay_eventos) or (en_sesion and not conteos_validos)

    # Última corrida de GitHub Actions, SOLO para mostrarla como dato al
    # lado (respaldo del escaneo desde el 21/9): ya no decide el estado del
    # Hunter -- eso lo hace su escaneo del VPS, más abajo. Sin respuesta de
    # Actions y sin copia en caché, simplemente no se muestra ese dato.
    cache_dir = cache_velas_segura(cfg.get("cache_velas"), problemas)
    if gha is None:
        def gha():
            if not cfg.get("gha_repo"):
                return {"corrida": None, "obtenido": None, "origen": None,
                        "error": "GitHub Actions no configurado (DASH_GHA_REPO)"}
            return dg.obtener(cfg["gha_repo"], cfg.get("gha_workflow", "momentum_hunter.yml"),
                              ahora, cache_dir, cfg.get("gha_ttl_seg", 300.0))
    hunter_gha = gha()
    corrida = hunter_gha.get("corrida")
    gha_momento = parse_ts(corrida["terminada"]) if corrida else None

    # El escaneo corre en el VPS (2026-09-21): el estado del Hunter sale de
    # su telemetría de hoy. GitHub solo es respaldo; su última corrida se
    # muestra al lado como dato, sin decidir el estado. Sin escaneo del VPS
    # hoy: "Sin datos", aunque la watchlist o GitHub sean frescos.
    escaneo = ultimo_escaneo_vps(cfg.get("telem_hunter"), ahora)
    hunter_momento = escaneo["fin"] if escaneo else None
    if escaneo:
        slot = f" · tanda {escaneo['slot']} de {escaneo['n_slots']}" if escaneo.get("slot") is not None else ""
        detalle_hunter = (f"escaneo VPS {_cuando(hunter_momento, cfg['tz'], ahora)}{slot}"
                          f" · {escaneo['evaluadas']} evaluadas · watchlist {_cuando(wl_momento, cfg['tz'], ahora)}")
    else:
        detalle_hunter = f"sin escaneo del VPS hoy · watchlist {_cuando(wl_momento, cfg['tz'], ahora)}"
    # El discovery corre en el VPS desde el 21/9. La corrida de GitHub es
    # del respaldo: se muestra aparte y en gris, rotulada "histórico",
    # para que "GitHub #243 del lun" junto a un OK no se lea como la
    # corrida que respalda ese OK (2026-09-29).
    nota_hunter = (f"respaldo en GitHub (no se usa): corrida #{corrida['numero']} {_cuando(gha_momento, cfg['tz'], ahora)}"
                   if corrida else None)
    # GitHub Actions (momentum_hunter.yml) dejó de ser el escáner el 21/9:
    # ahora escanea el VPS y GitHub quedó SOLO como respaldo manual, que
    # legítimamente puede pasar horas sin correr. Por eso su atraso ya NO
    # pinta el Hunter en rojo ni entra a "problemas" -- antes daba una
    # alerta permanente falsa ("GitHub lleva N min sin correr") aunque el
    # escaneo del VPS estuviera fresco (2026-09-24). La salud del Hunter la
    # decide su escaneo del VPS; la última corrida de GitHub sigue arriba
    # como dato informativo.
    estado_hunter = _estado_frescura(_edad_min(ahora, hunter_momento), cfg["hunter_max_min"], en_sesion)
    etapas = [
        {
            "nombre": "Hunter", "donde": "VPS",
            "rol": "Busca candidatos. Determinista, sin IA ni bróker.",
            "estado": estado_hunter,
            "detalle": detalle_hunter,
            "nota": nota_hunter,
        },
        {
            "nombre": "Rechequeo", "donde": "VPS",
            "rol": "Revisa la lista de candidatos (sin buscar nuevos).",
            # Un persist fallido es alerta aunque la corrida sea fresca: el
            # bot corrió, pero su estado no llegó a main.
            "estado": "alerta" if persist_fallidos else estado_rechequeo,
            "detalle": (f"última corrida {_hora(ult_rechequeo, cfg['tz'], ahora=ahora)}"
                        + (f" · {len(persist_fallidos)} persist fallidos, último {persist_fallidos[-1]['hora']}"
                           if persist_fallidos else "")),
        },
        {
            "nombre": "Ejecutor", "donde": "VPS",
            "rol": "Consulta a la IA y decide si entra.",
            # Mismo criterio que Riesgo: con log y rechequeo reciente, 0
            # decisiones es un dato ("OK", "0 decisiones"). "Sin datos" solo
            # si falta el log o no hay un rechequeo reciente. Un fallo
            # sostenido de la IA es alerta aunque el rechequeo sea fresco:
            # el bot corrió y no pudo decidir.
            "estado": "alerta" if ia_fallos else ("ok" if conteos_validos else "sin-datos"),
            # Un conteo > 0 es real aunque el rechequeo esté viejo; un 0 no.
            "detalle": (
                (f"{len(decisiones)} decisiones hoy · última {_hora(ult_decision, cfg['tz'], ahora=ahora)}"
                 if hay_eventos and (decisiones or conteos_validos)
                 else "— decisiones hoy · última —")
                + (f" · {len(ia_fallos)} fallos de IA, último {ia_fallos[-1]['hora']}"
                   if ia_fallos else "")
            ),
        },
        {
            "nombre": "Riesgo", "donde": "Código",
            "rol": "Límites deterministas. Sin margen. Fail-closed.",
            # El color sale SOLO de lo activo (últimos 3 ciclos). Un
            # DATO_FALTANTE de la mañana, ya parado, no deja la tarjeta
            # en rojo el resto del día (2026-09-28). MERCADO_CERRADO
            # activo y nada más es informativo, no "Revisar" ni capacidad
            # llena. Sin ciclo reciente en sesión: "sin datos", nunca verde.
            "estado": _estado_riesgo(riesgo, sin_datos_recientes, conteos_validos),
            "detalle": _detalle_riesgo(riesgo, hay_eventos, conteos_validos, en_sesion),
        },
    ]

    stream = []
    for o in (lista_ordenes or [])[:12]:
        stream.append({
            "hora": _hora(parse_ts(o.get("submitted_at")), cfg["tz"], segundos=True, ahora=ahora),
            "ticker": o.get("symbol") or "—",
            "lado": {"buy": "compra", "sell": "venta"}.get(o.get("side"), o.get("side") or "—"),
            "estado": ESTADOS_ORDEN.get(o.get("status"), o.get("status") or "—"),
            "precio": num(o.get("filled_avg_price")),
        })

    dudas = [
        {"ticker": d.get("ticker") or "—", "hora": _hora(d["_ts"], cfg["tz"], ahora=ahora),
         "motivo": str(d.get("motivo") or "sin motivo registrado")}
        for d in reversed(decisiones) if d.get("entra") is False
    ][:6]

    lista_posiciones = posiciones if isinstance(posiciones, list) else []
    abiertas_lista = abiertas if isinstance(abiertas, list) else []
    # Marcas (fill, stop): hoy + abiertas. El historial cerrado NO entra
    # aquí: un stop ya filled de un trade viejo no es el stop de ahora.
    todas_ordenes = _unir_ordenes(lista_ordenes or [], abiertas_lista)
    if velas is None:
        def velas(ticker):
            return dv.obtener(ticker, ahora, cache_dir, cfg.get("velas_ttl_seg", 120.0),
                              pausa_seg=cfg.get("velas_pausa_seg", 900.0),
                              pausa_bot=cfg.get("pausa_bot"))
    # Gráficos solo de lo que sigue vivo. El 28/9 DLB, NBIS y TWST se
    # pintaban "en operación" porque tuvieron orden hoy, ya cerradas.
    if err_p is None:
        tickers_op = tickers_en_operacion(
            lista_posiciones, abiertas_lista if err_a is None else [])
    else:
        tickers_op = []
    decisiones_orden = ordenes_enviadas_de_hoy(eventos)
    tope = int(cfg.get("velas_max_tickers", 6))
    operaciones = [{
        "ticker": t,
        "rol": rol,
        "velas": velas(t),
        "marcas": marcas_de(t, lista_posiciones, todas_ordenes, watch_todas, decisiones_orden.get(t)),
    } for t, rol in tickers_op[:tope]]
    omitidos = [t for t, _rol in tickers_op[tope:]]
    # Aviso del feed de velas: uno solo para la sección, no uno por ticker.
    avisos_feed = sorted({op["velas"].get("aviso_feed") for op in operaciones if op["velas"].get("aviso_feed")})

    # Columna Stop y aviso: el listado de `ordenes_de_simbolos`. Si el
    # GET falla, `None` — nunca `[]`, porque una lista vacía afirmaría
    # que no hay stop.
    if err_p is None and _cobertura is not None:
        ordenes_simbolo, err_s = leer_ordenes_de_simbolos(get, _simbolos_lista(lista_posiciones))
        if err_s and err_s not in problemas:
            problemas.append(err_s)
    else:
        ordenes_simbolo, err_s = None, None
    if not isinstance(ordenes_simbolo, list):
        ordenes_simbolo = None
    filas_pos = None if err_p is not None else [
        fila_posicion(p, ordenes_simbolo, ordenes_conocidas=ordenes_simbolo is not None)
        for p in lista_posiciones if isinstance(p, dict) and p.get("symbol")]
    filas_pend = None if err_a is not None else filas_pendientes(abiertas_lista, _simbolos(lista_posiciones))
    if err_p is not None or (err_o is not None and err_h is not None):
        filas_cerradas = None
    else:
        fuentes_cierre = []
        if isinstance(ordenes, list):
            fuentes_cierre.append(ordenes)
        if isinstance(historicas, list):
            fuentes_cierre.append(historicas)
        filas_cerradas = cierres_de_hoy(
            _unir_aplanadas(*fuentes_cierre), _simbolos(lista_posiciones), desde)
    limites = uso_de_limites(
        lista_posiciones if err_p is None else None,
        abiertas_lista if err_a is None else None,
        filas_pos, equity, num(cuenta.get("cash")) if isinstance(cuenta, dict) else None,
        _CONFIG_PAPER,
    )
    avisos, nota_seguimiento = contrastar_broker(
        lista_posiciones if err_p is None else None,
        ordenes_simbolo,
        cfg.get("revisiones"),
    )

    return {
        "ahora": ahora, "tz": cfg["tz"], "en_sesion": en_sesion, "alpaca_ok": alpaca_ok,
        "operaciones": operaciones, "operaciones_omitidas": omitidos, "avisos_feed": avisos_feed,
        "hay_alpaca_operaciones": err_p is None,
        "posiciones_broker": filas_pos,
        "pendientes_broker": filas_pend,
        "cerradas_hoy": filas_cerradas,
        "avisos_broker": avisos,
        "nota_seguimiento": nota_seguimiento,
        "limites": limites,
        "minutos_entrada_max": (getattr(_CONFIG_PAPER, "minutos_maximos_entrada_sin_llenar", None)
                                if _CONFIG_PAPER is not None else None),
        "fuente_datos": fuente_datos_activa(cfg.get("telem_hunter"), ahora),
        "problemas": problemas, "etapas": etapas,
        "equity": equity, "pnl": pnl, "pnl_pct": pnl_pct, "cuenta_numero": cuenta_numero,
        "desajuste_equity": _desajuste_equity(equity_mes, equity, last_equity),
        "n_pos": n_pos, "n_ord": n_ord, "n_rech": n_rech,
        "watch": watch, "wl_momento": wl_momento,
        "hunter_gha": hunter_gha, "hunter_momento": hunter_momento, "hunter_escaneo": escaneo,
        "lat": lat, "lat_detalle": lat_detalle,
        "lat_sin_barra": compras_sin_latencia(eventos, lista_ordenes, hay_eventos),
        "lat_mediana": statistics.median(valores) if valores else None,
        "lat_p90": percentil(valores, 90),
        "lat_fuera": sum(1 for v in valores if v > presupuesto) if valores else None,
        "presupuesto": presupuesto,
        "stream": stream, "dudas": dudas,
        "equity_dia": equity_dia, "equity_mes": equity_mes,
        "bloqueos": sorted(por_limite.items(), key=lambda kv: -kv[1]),
        "riesgo": riesgo,
        "stop_diario": resumir_stop_diario(eventos, cfg["tz"], ahora),
        "aprendizaje": leer_aprendizaje(cfg.get("aprendizaje"), eventos, ahora),
        "historial_trades": leer_historial_trades(cfg.get("aprendizaje")),
        "persist_fallidos": persist_fallidos,
        "ia_fallos": ia_fallos,
        "hay_eventos": hay_eventos,
        "conteos_validos": conteos_validos, "motivo_sin_datos": motivo_sin_datos,
        "sin_datos_recientes": sin_datos_recientes,
        "ult_bloqueo": bloqueos[-1] if bloqueos else None,
    }


# ───────────────────────── curva de equity ─────────────────────────

RUTA_HISTORIAL = "/v2/account/portfolio/history"


def serie_equity(datos, ahora: datetime | None = None) -> tuple[list[tuple[datetime, float]], float | None]:
    """(puntos [(momento, equity)], equity inicial) a partir de la respuesta
    de `GET /v2/account/portfolio/history`.

    Un punto sin marca de tiempo o sin equity se salta. Un equity en 0 o
    negativo también: Alpaca rellena con 0 los tramos donde la cuenta no
    tenía valor (antes de fondearla, fuera de sesión), y una cuenta paper
    de verdad nunca vale 0. Dibujar esos ceros sería inventar una caída a
    cero que no ocurrió. Un punto posterior a `ahora` tampoco se dibuja:
    una equity "futura" no puede ser un dato medido. `base_value` es la
    equity al inicio del periodo, tal cual la manda Alpaca; si no viene,
    no se inventa."""
    if not isinstance(datos, dict):
        return [], None
    marcas, valores = datos.get("timestamp"), datos.get("equity")
    if not isinstance(marcas, list) or not isinstance(valores, list):
        return [], None
    puntos = []
    for marca, valor in zip(marcas, valores):
        momento, equity = parse_ts(marca), num(valor)
        if momento is None or equity is None or equity <= 0:
            continue
        if ahora is not None and momento > ahora:
            continue
        puntos.append((momento, equity))
    puntos.sort(key=lambda p: p[0])
    base = num(datos.get("base_value"))
    return puntos, (base if base is not None and base > 0 else None)


def _historial_equity(get, params: dict, problemas: list, ahora: datetime) -> dict:
    """`sesion` es la fecha (de Nueva York, que es la del mercado) del
    último punto real, y `es_hoy` si esa fecha es la de `ahora`. Hace
    falta porque `period=1D` NO significa "hoy": Alpaca devuelve el
    último día de mercado, y antes de la apertura ese día es el anterior
    (una madrugada de lunes trae la sesión completa del viernes). El
    panel tiene que decir de qué día son los puntos, no suponerlo."""
    datos, err = get(RUTA_HISTORIAL, params)
    if err:
        if err not in problemas:
            problemas.append(err)
        return {"puntos": [], "base": None, "error": err, "sesion": None, "es_hoy": False}
    puntos, base = serie_equity(datos, ahora)
    sesion = puntos[-1][0].astimezone(NY).date() if puntos else None
    return {"puntos": puntos, "base": base, "error": None, "sesion": sesion,
            "es_hoy": sesion is not None and sesion == ahora.astimezone(NY).date()}


def con_punto_en_vivo(hist: dict, equity: float | None, ahora: datetime) -> dict:
    """Agrega el equity de ahora como punto en vivo de la gráfica de hoy
    (`vivo`: (momento, equity)), aparte de `puntos`, que sigue siendo el
    historial tal cual lo manda Alpaca.

    El historial de 5 min termina en la última vela cerrada: la tarjeta
    "P&L del día" decía +$13.60 (equity en vivo) y la gráfica +$11.93 (la
    vela de las HH:MM), la misma cuenta con dos números (2026-09-29). Solo
    si la gráfica es de HOY, el equity se leyó y ahora es posterior al
    último punto. Un historial de otra sesión o sin equity no se toca:
    no se pega un dato de hoy a la sesión del viernes."""
    if not hist.get("es_hoy") or equity is None or equity <= 0 or not hist.get("puntos"):
        return hist
    if ahora <= hist["puntos"][-1][0]:
        return hist
    return {**hist, "vivo": (ahora, equity)}


def ultimo_escaneo_vps(dir_telemetria: Path | None, ahora: datetime) -> dict | None:
    """Último escaneo completo del VPS de HOY (fecha UTC, como escribe
    momentum_hunter.telemetria): {"fin", "inicio", "slot", "n_slots",
    "evaluadas"}. Sin archivo, sin registros de modo "escaneo" o con un
    timestamp ilegible: None. Nunca se estima nada."""
    if dir_telemetria is None:
        return None
    ruta = Path(dir_telemetria) / ahora.astimezone(timezone.utc).date().isoformat() / "vps" / "events.jsonl"
    try:
        lineas = ruta.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    ultimo = None
    for linea in lineas:
        try:
            r = json.loads(linea)
        except ValueError:
            continue
        if not isinstance(r, dict) or r.get("modo") != "escaneo":
            continue
        fin = parse_ts(r.get("timestamp"))
        if fin is None:
            continue
        embudo = r.get("embudo") if isinstance(r.get("embudo"), dict) else {}
        evaluadas = embudo.get("evaluadas") if isinstance(embudo.get("evaluadas"), dict) else {}
        registro = {"fin": fin, "inicio": parse_ts(r.get("inicio_ts")),
                    "slot": r.get("slot") if isinstance(r.get("slot"), int) else None,
                    "n_slots": r.get("n_slots") if isinstance(r.get("n_slots"), int) else None,
                    "evaluadas": sum(v for v in evaluadas.values() if isinstance(v, (int, float)))}
        if ultimo is None or registro["fin"] >= ultimo["fin"]:
            ultimo = registro
    return ultimo


def _fecha_corta(fecha) -> str:
    """"vie 18 sep": el día con nombre para que una sesión vieja nunca se
    lea como la de hoy."""
    return f"{DIAS_ES[fecha.weekday()]} {fecha.day} {MESES_ES[fecha.month - 1]}"


def _etiqueta_x(momento: datetime, tz, modo: str) -> str:
    local = momento.astimezone(tz)
    if modo == "dia":
        return local.strftime("%H:%M")
    return f"{local.day} {MESES_ES[local.month - 1]}"


def _desajuste_equity(hist: dict, equity: float | None, last_equity: float | None) -> str | None:
    """Aviso cuando el historial y la cuenta no cuentan la misma historia.

    El último punto del historial diario es, por construcción, o el valor de
    hoy (≈ `equity`) o el cierre anterior (= `last_equity`). Si no se parece
    a ninguno de los dos (0,5 % de tolerancia por el desfase de segundos
    entre las dos consultas), algo está mal: lo más probable es que las
    credenciales del panel apunten a otra cuenta paper que las del
    ejecutor. No se corrige ni se oculta nada: se avisa con los dos números
    para que la persona lo compruebe."""
    if not hist.get("puntos"):
        return None
    ultimo = hist["puntos"][-1][1]
    referencias = [r for r in (equity, last_equity) if r is not None and r > 0]
    if not referencias or any(abs(ultimo - r) / r <= 0.005 for r in referencias):
        return None
    return (f"El historial no cuadra con la cuenta: último cierre del historial {fmt_dinero(ultimo)} "
            f"vs equity {fmt_dinero(equity)} / cierre anterior {fmt_dinero(last_equity)}. "
            "Confirmar que el panel y el ejecutor usan la misma cuenta paper.")


def _grafico_equity(hist: dict, tz, modo: str) -> str:
    """Línea de equity en SVG, sin librerías. `modo` es "dia" (velas de 5
    min) o "mes" (cierre diario) y solo cambia las etiquetas del eje X.

    Sin puntos: "Sin datos" en el área del gráfico y ninguna línea. Con
    puntos: la línea va de min a max REALES (una serie plana se dibuja
    plana, con un margen fijo para que no quede pegada al borde), y la
    equity inicial se marca con una línea punteada."""
    ancho, alto, x0, x1, y0, y1 = 480, 230, 62, 470, 196, 14
    etiqueta = {"dia": "Equity de hoy, velas de 5 minutos", "mes": "Equity del último mes, cierre diario"}[modo]
    partes = [f'<svg viewBox="0 0 {ancho} {alto}" role="img" aria-label="{esc(etiqueta)}">',
              f'<line x1="{x0}" y1="{y1}" x2="{x0}" y2="{y0}" class="rejilla"/>',
              f'<line x1="{x0}" y1="{y0}" x2="{x1}" y2="{y0}" class="rejilla"/>']
    puntos, base = hist["puntos"], hist["base"]
    if not puntos:
        motivo = "Alpaca no respondió" if hist.get("error") else "sin historial de equity"
        partes.append(f'<text x="{(x0+x1)/2}" y="110" text-anchor="middle" class="eje">Sin datos</text>')
        partes.append(f'<text x="{(x0+x1)/2}" y="128" text-anchor="middle" class="eje">{esc(motivo)}</text>')
        partes.append("</svg>")
        return "".join(partes)

    vivo = hist.get("vivo")
    valores = [v for _, v in puntos]
    candidatos = valores + ([base] if base is not None else []) + ([vivo[1]] if vivo else [])
    minimo, maximo = min(candidatos), max(candidatos)
    if maximo - minimo < 1e-9:
        # Serie plana de verdad: margen del 0,5 % (mínimo $1) a cada lado
        # para que la línea se vea, sin cambiar su forma.
        margen = max(1.0, minimo * 0.005)
    else:
        margen = (maximo - minimo) * 0.08
    lo, hi = minimo - margen, maximo + margen
    t_ini, t_fin = puntos[0][0].timestamp(), (vivo[0] if vivo else puntos[-1][0]).timestamp()

    def y(v: float) -> float:
        return y0 - (v - lo) / (hi - lo) * (y0 - y1)

    def x(t: float) -> float:
        # Un solo punto (o todos en el mismo instante): al centro.
        if t_fin - t_ini < 1:
            return (x0 + x1) / 2
        return x0 + (t - t_ini) / (t_fin - t_ini) * (x1 - x0)

    for valor in (minimo, maximo):
        partes.append(f'<text x="{x0-6}" y="{y(valor)+4:.1f}" text-anchor="end" class="eje">{esc(fmt_dinero(valor))}</text>')
    if base is not None:
        yb = y(base)
        partes.append(f'<line x1="{x0}" y1="{yb:.1f}" x2="{x1}" y2="{yb:.1f}" class="base-punteada" stroke-width="1" stroke-dasharray="5 4"/>')
        partes.append(f'<text x="{x1}" y="{yb-6:.1f}" text-anchor="end" class="eje">inicial {esc(fmt_dinero(base))}</text>')
    coords = " ".join(f"{x(t.timestamp()):.1f},{y(v):.1f}" for t, v in puntos)
    partes.append(f'<polyline points="{coords}" fill="none" class="serie" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
    if len(puntos) == 1 and not vivo:
        partes.append(f'<circle cx="{x(t_ini):.1f}" cy="{y(valores[0]):.1f}" r="3" class="serie-punto"/>')
    if vivo:
        # Tramo punteado de la última vela cerrada al equity de ahora.
        xa, ya = x(puntos[-1][0].timestamp()), y(valores[-1])
        xv, yv = x(vivo[0].timestamp()), y(vivo[1])
        partes.append(f'<line class="serie-vivo" x1="{xa:.1f}" y1="{ya:.1f}" x2="{xv:.1f}" y2="{yv:.1f}" stroke-width="2" stroke-dasharray="3 3"/>')
        partes.append(f'<circle cx="{xv:.1f}" cy="{yv:.1f}" r="3.5" class="serie-punto"/>')
    partes.append(f'<text x="{x0}" y="{alto-6}" class="eje">{esc(_etiqueta_x(puntos[0][0], tz, modo))}</text>')
    if len(puntos) > 1 or vivo:
        fin = vivo[0] if vivo else puntos[-1][0]
        partes.append(f'<text x="{x1}" y="{alto-6}" text-anchor="end" class="eje">'
                      f'{esc(_etiqueta_x(fin, tz, modo))}{" en vivo" if vivo else ""}</text>')
    partes.append("</svg>")
    return "".join(partes)


def _resumen_equity(hist: dict, tz=None) -> str:
    """Inicial / último / variación bajo cada gráfico. Solo con datos
    reales: sin base o sin puntos, "—"."""
    puntos, base = hist["puntos"], hist["base"]
    vivo = hist.get("vivo")
    ultimo = vivo[1] if vivo else (puntos[-1][1] if puntos else None)
    variacion = ultimo - base if ultimo is not None and base is not None else None
    clase = "" if variacion is None else ("pos" if variacion >= 0 else "neg")
    pct = "" if variacion is None or not base else f" ({variacion / base * 100:+.2f}%)"
    if vivo:
        etiqueta_ultimo = f"Último · en vivo {vivo[0].astimezone(tz):%H:%M}" if tz is not None else "Último · en vivo"
    elif puntos and tz is not None:
        # Sin punto en vivo, el último es el cierre de una vela: se dice cuál.
        etiqueta_ultimo = f"Último · vela de las {puntos[-1][0].astimezone(tz):%H:%M}"
    else:
        etiqueta_ultimo = "Último"
    return (f'<div class="stats"><div><span class="mono">Inicial</span><b>{esc(fmt_dinero(base))}</b></div>'
            f'<div><span class="mono">{esc(etiqueta_ultimo)}</span><b>{esc(fmt_dinero(ultimo))}</b></div>'
            f'<div><span class="mono">Variación</span><b class="{clase}">{esc(fmt_dinero(variacion, signo=True) + pct)}</b></div></div>')


# ───────────────────────── velas del ticker en operación ─────────────────────────

# Los mismos tipos que `reconciliacion.cobertura`. Un `limit` es el
# take-profit, no el stop.
TIPOS_STOP = ("stop", "stop_limit", "trailing_stop")


def _unir_ordenes(*listas: list) -> list[dict]:
    vistas, out = set(), []
    for lista in listas:
        for o in lista:
            if not isinstance(o, dict):
                continue
            clave = o.get("id") or id(o)
            if clave in vistas:
                continue
            vistas.add(clave)
            out.append(o)
    return out


def cache_velas_segura(ruta: Path | None, problemas: list) -> Path:
    """La caché de velas jamás dentro del repo. Si la configuración apunta
    adentro (por error o por una ruta relativa con el cwd en el árbol), se
    usa el temporal del sistema y se avisa."""
    ruta = Path(ruta) if ruta else CACHE_VELAS_DEFECTO
    try:
        dentro = ruta.resolve().is_relative_to(REPO)
    except (OSError, RuntimeError):
        dentro = False
    if dentro:
        problemas.append(f"DASH_CACHE_VELAS apunta dentro del repo ({ruta}); "
                         f"la caché de velas se guarda en {CACHE_VELAS_DEFECTO}.")
        return CACHE_VELAS_DEFECTO
    return ruta


# Claves que un proveedor de barras podría dejar en la telemetría del
# hunter. El PR que suma Alpaca SIP todavía no está en main: si ninguna
# está, no se afirma "Yahoo". `fuente` NO entra: en este repo es el
# escritor (vps/gha), no el feed de precios.
_CLAVES_FUENTE = (
    "proveedor", "proveedor_datos", "proveedor_barras", "proveedor_velas",
    "fuente_datos", "fuente_velas", "data_provider", "feed",
)
_AUSENTE = object()


def _unir_aplanadas(*listas: list) -> list[dict]:
    """Quita duplicados por id. La misma orden viene en 'hoy' y en
    'closed'; contarla dos veces doblaría el P&L.

    El aplanado es `ordenes_con_patas`: la pata hereda `_symbol` del
    padre. El FIFO de cierres lee `symbol`, así que se copia ahí cuando
    la pata no lo trae."""
    vistos: set[str] = set()
    out: list[dict] = []
    for lista in listas:
        patas = _ordenes_con_patas(lista) if _ordenes_con_patas is not None else []
        for o in patas:
            if not o.get("symbol") and isinstance(o.get("_symbol"), str) and o.get("_symbol"):
                o["symbol"] = o["_symbol"]
            oid = o.get("id")
            if oid:
                if oid in vistos:
                    continue
                vistos.add(str(oid))
            out.append(o)
    return out


def leer_ordenes_de_simbolos(get, simbolos: list[str]):
    """(lista | None, error | None). La consulta de `ordenes_de_simbolos`.

    None en la lista: no se pudieron leer, y eso no es "no hay stop".
    Sin símbolos no se llama al broker. Un cuerpo que no es una lista
    tampoco cuenta como "cero órdenes"."""
    if _parametros_ordenes_de_simbolos is None:
        return None, ("no se pudo cargar la consulta de órdenes del paper trader; "
                      "no se afirma que falte el stop")
    params = _parametros_ordenes_de_simbolos(simbolos)
    if params is None:
        return [], None
    datos, err = get("/v2/orders", params)
    if err:
        return None, err
    if not isinstance(datos, list):
        return None, ("Alpaca no devolvió una lista de órdenes de los símbolos en posición; "
                      "no se afirma que falte el stop")
    return datos, None


def _simbolos(posiciones: list) -> set[str]:
    return set(_simbolos_lista(posiciones))


def _simbolos_lista(posiciones: list) -> list[str]:
    """Símbolos en el orden del broker, sin repetir. Vacía: no se pide
    `status=all` sin filtro."""
    salida: list[str] = []
    vistos: set[str] = set()
    for p in posiciones:
        if not isinstance(p, dict):
            continue
        simbolo = p.get("symbol")
        if not isinstance(simbolo, str) or not simbolo or simbolo in vistos:
            continue
        vistos.add(simbolo)
        salida.append(simbolo)
    return salida


def _mas_reciente(candidatas: list[dict]) -> dict | None:
    if not candidatas:
        return None
    minimo = datetime.min.replace(tzinfo=timezone.utc)

    def clave(o):
        return parse_ts(o.get("submitted_at")) or parse_ts(o.get("created_at")) or minimo

    return max(candidatas, key=clave)


def _nivel_de(orden: dict | None, precio_clave: str) -> dict | None:
    if orden is None:
        return None
    status = orden.get("status")
    return {
        "precio": num(orden.get(precio_clave)),
        "estado": str(status).lower() if status else None,
    }


def salidas_de(ticker: str, ordenes: list | None) -> dict:
    """Stop, take-profit y venta a mercado todavía vivos de `ticker`.

    La protección la decide `cobertura` (la de `detectar`): whitelist
    `held` / `new` / `accepted` / `pending_new` en la fila o en una pata.
    Un stop en `pending_cancel` o con un status desconocido no entra, así
    que no llena la columna. Un `limit` es el take-profit. `ordenes is
    None` (el GET falló) no es "no hay salida"."""
    vacio = {"stop": None, "tp": None, "mercado": False}
    if ordenes is None or _cobertura is None or _ventas_vivas is None:
        return vacio
    cob = _cobertura(ordenes)
    if cob is None:
        return vacio
    con_stop, con_mercado = cob
    stops, limites = [], []
    for o in _ventas_vivas(ordenes) or []:
        if o.get("_symbol") != ticker:
            continue
        tipo = str(o.get("type") or "").lower()
        if tipo in TIPOS_STOP:
            stops.append(o)
        elif tipo == "limit":
            limites.append(o)
    stop = _nivel_de(_mas_reciente(stops), "stop_price") if ticker in con_stop else None
    return {
        "stop": stop,
        "tp": _nivel_de(_mas_reciente(limites), "limit_price"),
        "mercado": ticker in con_mercado,
    }


def riesgo_de(entrada: float | None, stop: float | None, objetivo: float | None,
              qty: float | None) -> dict:
    """Dólares que se pierden si toca el stop, y la relación
    beneficio/riesgo (R) hasta el objetivo. Solo largos: este bot no
    abre cortos. Cualquier dato que falte deja su resultado en None.

    Un stop en o por encima de la entrada no es riesgo: es ganancia
    asegurada (`riesgo` <= 0) y el R no tiene sentido (None)."""
    riesgo = rr = None
    if None not in (entrada, stop, qty):
        riesgo = round((entrada - stop) * qty, 2)
        if entrada - stop > 1e-9 and objetivo is not None:
            rr = (objetivo - entrada) / (entrada - stop)
    return {"riesgo": riesgo, "rr": rr}


def _precio(nivel: dict | None) -> float | None:
    return nivel.get("precio") if isinstance(nivel, dict) else None


def fila_posicion(p: dict, ordenes_vivas: list, ordenes_conocidas: bool = True) -> dict:
    """Una posición tal como la manda Alpaca. El P&L abierto es el campo
    `unrealized_pl`; si falta, es None, no un cálculo con un precio que
    no vino. `unrealized_plpc` es fracción (0,0125 = 1,25 %), igual que
    lo lee `cierre.py`. Si las órdenes no se pudieron leer, stop y
    objetivo quedan desconocidos: un "—" ahí se leería como que no hay."""
    # `None` es un GET que falló: no se sustituye por [] (eso diría que
    # no hay stop). Solo una lista leída de verdad llena la columna.
    if ordenes_conocidas and ordenes_vivas is not None:
        salidas = salidas_de(str(p.get("symbol")), ordenes_vivas)
    else:
        salidas = {"stop": None, "tp": None, "mercado": False}
    plpc = num(p.get("unrealized_plpc"))
    qty, entrada = num(p.get("qty")), num(p.get("avg_entry_price"))
    return {
        "ticker": p.get("symbol"),
        "qty": qty,
        "entrada": entrada,
        "actual": num(p.get("current_price")),
        "valor": num(p.get("market_value")),
        "pnl": num(p.get("unrealized_pl")),
        "pnl_pct": None if plpc is None else plpc * 100,
        "stop": salidas["stop"],
        "tp": salidas["tp"],
        "mercado": salidas["mercado"],
        "salidas_conocidas": ordenes_conocidas,
        **riesgo_de(entrada, _precio(salidas["stop"]), _precio(salidas["tp"]), qty),
    }


def filas_pendientes(abiertas: list, simbolos_abiertos: set[str]) -> list[dict]:
    """Compras de entrada todavía vivas, y cualquier orden de arriba cuyo
    símbolo no esté abierto (no es la pata de salida de una posición).

    Las patas `held` no son filas propias: se muestran como stop y
    objetivo de la orden padre. El take-profit `new` de una posición
    abierta tampoco: va en la fila de esa posición."""
    filas = []
    for o in abiertas:
        if not isinstance(o, dict) or not (_orden_sigue_viva and _orden_sigue_viva(o)):
            continue
        simbolo = o.get("symbol")
        if not isinstance(simbolo, str) or not simbolo:
            continue
        lado = str(o.get("side") or "").lower()
        if lado != "buy" and simbolo in simbolos_abiertos:
            continue
        if lado not in ("buy", "sell"):
            continue
        salidas = salidas_de(simbolo, [o])
        status = o.get("status")
        qty, limite = num(o.get("qty")), num(o.get("limit_price"))
        # El riesgo de una compra pendiente es el que tendría si llena al
        # límite. Una venta pendiente no abre riesgo nuevo.
        riesgo = (riesgo_de(limite, _precio(salidas["stop"]), _precio(salidas["tp"]), qty)
                  if lado == "buy" else {"riesgo": None, "rr": None})
        filas.append({
            "ticker": simbolo,
            "lado": lado,
            "qty": qty,
            "limite": limite,
            "stop": salidas["stop"],
            "tp": salidas["tp"],
            "mercado": salidas["mercado"],
            "estado": str(status).lower() if status else None,
            "colocada": parse_ts(o.get("submitted_at")) or parse_ts(o.get("created_at")),
            **riesgo,
        })
    return filas


def _qty_fill(orden: dict) -> float | None:
    """Cantidad que de verdad se ejecutó. `filled_qty` manda. Si falta y
    el estado es `filled`, `qty` es el tamaño de esa orden ya llena. Si
    faltan los dos, None: no se usa 0."""
    q = num(orden.get("filled_qty"))
    if q is not None:
        return q
    if str(orden.get("status") or "").lower() == "filled":
        return num(orden.get("qty"))
    return None


def cierres_de_hoy(ordenes: list, simbolos_abiertos: set[str], desde: datetime) -> list[dict]:
    """Posiciones que ya no están abiertas y tuvieron una venta llena
    hoy (día de Nueva York, el mismo corte que las órdenes de hoy).

    El P&L es FIFO sobre los fills que alcanzamos a ver: (salida −
    entrada) × cantidad, de un largo. Este bot no abre cortos. Si el
    historial no alcanza para emparejar toda la venta, entrada y P&L
    quedan en None; la salida sí se muestra si el fill de hoy la trae.
    Un símbolo que sigue en `posiciones` no entra aquí aunque haya
    vendido una parte: la posición abierta manda."""
    fills = [
        o for o in ordenes
        if isinstance(o, dict) and str(o.get("status") or "").lower() == "filled" and parse_ts(o.get("filled_at"))
    ]
    fills.sort(key=lambda o: parse_ts(o.get("filled_at")) or datetime.min.replace(tzinfo=timezone.utc))
    por: dict[str, list] = {}
    for o in fills:
        simbolo = o.get("symbol")
        if isinstance(simbolo, str) and simbolo:
            por.setdefault(simbolo, []).append(o)

    filas = []
    for simbolo, serie in por.items():
        if simbolo in simbolos_abiertos:
            continue
        lots: list = []
        qty_hoy = 0.0
        qty_hoy_ok = True
        notional_salida = 0.0
        qty_salida_preciada = 0.0
        notional_entrada = 0.0
        qty_emparejada = 0.0
        pnl_ok = True
        ultima = None
        ventas_hoy = 0
        for o in serie:
            ts = parse_ts(o.get("filled_at"))
            lado = str(o.get("side") or "").lower()
            qty = _qty_fill(o)
            precio = num(o.get("filled_avg_price"))
            hoy = ts is not None and ts >= desde
            usable = qty is not None and precio is not None and qty > 0
            if lado == "buy":
                lots.append([qty, precio] if usable else None)
                continue
            if lado != "sell":
                continue
            if hoy:
                ventas_hoy += 1
                ultima = ts
                if qty is None or qty <= 0:
                    qty_hoy_ok = False
                    pnl_ok = False
                else:
                    qty_hoy += qty
                if usable:
                    notional_salida += precio * qty
                    qty_salida_preciada += qty
                else:
                    pnl_ok = False
            if not usable:
                # Un hueco en el FIFO invalida lo que se empareje después:
                # no sabemos qué lote quedaba.
                lots.clear()
                lots.append(None)
                if hoy:
                    pnl_ok = False
                continue
            restante = qty
            while restante > 1e-8 and lots:
                lot = lots[0]
                if lot is None:
                    pnl_ok = False
                    lots.pop(0)
                    break
                tomar = min(restante, lot[0])
                if hoy:
                    notional_entrada += tomar * lot[1]
                    qty_emparejada += tomar
                lot[0] -= tomar
                restante -= tomar
                if lot[0] <= 1e-8:
                    lots.pop(0)
            if restante > 1e-8:
                pnl_ok = False
        if ventas_hoy == 0:
            continue
        salida = (notional_salida / qty_salida_preciada) if qty_salida_preciada > 0 and qty_hoy_ok else None
        if pnl_ok and qty_emparejada > 0 and qty_hoy_ok and abs(qty_emparejada - qty_hoy) <= 1e-6:
            entrada = notional_entrada / qty_emparejada
            pnl = round(notional_salida - notional_entrada, 2)
        else:
            entrada = None
            pnl = None
        filas.append({
            "ticker": simbolo,
            "qty": qty_hoy if qty_hoy_ok and qty_hoy > 0 else None,
            "entrada": entrada,
            "salida": salida,
            "pnl": pnl,
            "hora": ultima,
        })
    filas.sort(key=lambda f: f["hora"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return filas


def contrastar_broker(posiciones, ordenes, ruta_revisiones):
    """(avisos, nota). Reusa `reconciliacion.detectar`.

    `ordenes` es el listado anidado de `ordenes_de_simbolos` (el padre
    `filled` incluido). El detector mira la fila y sus `legs` y solo
    cuenta una venta viva; no se aplana antes, porque tirar el padre
    `filled` es como se perdía el stop `held`.

    Los avisos son dicts {ticker, sin_seguimiento, sin_stop}. `None` en
    avisos significa que no se pudo contrastar (no es "todo en orden").
    Sin ruta de revisiones no se lee el libro del repo: las pruebas del
    panel no lo configuran y no deben alarmar con el archivo real.

    `ordenes is None` (no se pudieron leer) se le pasa tal cual al
    detector: no afirma que falte el stop. Una lista vacía sí lo afirma."""
    if ruta_revisiones is None or not isinstance(posiciones, list):
        return [], None
    try:
        from momentum_paper_trader.estado import cargar as cargar_revisiones
        from momentum_paper_trader.reconciliacion import detectar
    except Exception as ex:
        return None, (f"no se pudo cargar la reconciliación ({type(ex).__name__}); "
                      "no se contrastó el broker con el seguimiento")
    try:
        revisiones = cargar_revisiones(Path(ruta_revisiones))
    except Exception as ex:
        return None, (f"no se pudieron leer las revisiones ({type(ex).__name__}); "
                      "no se afirma que el seguimiento cubra lo que el broker tiene abierto")
    problemas = detectar(posiciones, ordenes, revisiones)
    return [
        {"ticker": p.ticker, "sin_seguimiento": p.sin_seguimiento, "sin_stop": p.sin_stop}
        for p in problemas
    ], None


def _etiqueta_fuente(valor) -> str | None:
    """Una clave suelta (`proveedor`, `feed`…) de una telemetría que no
    trae el bloque `datos` de la PR #185."""
    if not isinstance(valor, str):
        return None
    texto = valor.strip()
    if not texto or len(texto) > 40:
        return None
    clave = texto.lower().replace(" ", "_").replace("-", "_")
    conocidas = {
        "yahoo": "Yahoo",
        "yfinance": "Yahoo",
        "yahoo_chart": "Yahoo",
        "alpaca_sip": "Alpaca SIP",
        "sip": "Alpaca SIP",
        "alpaca_data_sip": "Alpaca SIP",
        "iex": "Alpaca IEX",
        "mixto": "Mixto",
    }
    if clave in conocidas:
        return conocidas[clave]
    if clave == "alpaca":
        return "Alpaca"
    return texto


def _etiqueta_medida(fuente, feed) -> str | None:
    """Lo que contestó de verdad (`datos.fuente` + `datos.feed`).

    `alpaca` + `sip` es Alpaca SIP. `mixto` es el feed más el respaldo
    Yahoo de esa corrida. `iex` no se disfraza de SIP. Sin `fuente`
    medible no se usa `configurada`: estar configurado no es haber
    contestado."""
    if not isinstance(fuente, str) or not fuente.strip():
        return None
    f = fuente.strip().lower()
    feed_s = feed.strip().lower() if isinstance(feed, str) and feed.strip() else None
    if f == "yahoo":
        return "Yahoo"
    if f == "alpaca":
        if feed_s == "sip":
            return "Alpaca SIP"
        if feed_s == "iex":
            return "Alpaca IEX"
        return "Alpaca"
    if f == "mixto":
        if feed_s == "sip":
            return "Alpaca SIP + Yahoo"
        if feed_s == "iex":
            return "Alpaca IEX + Yahoo"
        return "Mixto"
    return _etiqueta_fuente(fuente)


def _valor_fuente_suelto(registro: dict):
    """Claves de un PR que todavía no usa el bloque `datos`. `fuente` a
    secas no entra: en la raíz del JSONL es el escritor (vps/gha)."""
    def en(d):
        if not isinstance(d, dict):
            return _AUSENTE
        for clave in _CLAVES_FUENTE:
            if clave in d and isinstance(d.get(clave), str) and str(d.get(clave)).strip():
                return d[clave]
        return _AUSENTE

    hallado = en(registro)
    if hallado is not _AUSENTE:
        return hallado
    for caja in ("embudo", "mercado"):
        hallado = en(registro.get(caja))
        if hallado is not _AUSENTE:
            return hallado
    return _AUSENTE


def fuente_datos_activa(dir_telemetria, ahora: datetime) -> str | None:
    """Yahoo o Alpaca SIP, si la telemetría de hoy del VPS lo midió.

    El esquema que escribe el hunter es `datos.fuente` / `datos.feed`
    (`yahoo`, `alpaca`, `mixto`, feed `sip` o `iex`). Un evento que no
    trae el bloque, o lo trae con `fuente` vacía, no se midió: se sigue
    hacia el anterior del mismo día. Si ninguno midió, None — no se
    asume Yahoo. `fuente: vps` en la raíz no es el feed de precios."""
    if dir_telemetria is None:
        return None
    ruta = Path(dir_telemetria) / ahora.astimezone(timezone.utc).date().isoformat() / "vps" / "events.jsonl"
    try:
        lineas = Path(ruta).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    eventos = []
    for linea in lineas:
        try:
            r = json.loads(linea)
        except ValueError:
            continue
        if not isinstance(r, dict):
            continue
        ts = parse_ts(r.get("timestamp"))
        if ts is None:
            continue
        eventos.append((ts, r))
    eventos.sort(key=lambda par: par[0], reverse=True)
    for _ts, r in eventos:
        datos = r.get("datos")
        if isinstance(datos, dict) and "fuente" in datos:
            etiqueta = _etiqueta_medida(datos.get("fuente"), datos.get("feed"))
            if etiqueta:
                return etiqueta
            continue
        suelto = _valor_fuente_suelto(r)
        if suelto is _AUSENTE:
            continue
        etiqueta = _etiqueta_fuente(suelto)
        if etiqueta:
            return etiqueta
    return None


def tickers_en_operacion(posiciones: list, ordenes_abiertas: list) -> list[tuple[str, str]]:
    """(ticker, rol) con rol 'abierta' o 'pendiente'.

    Abiertas primero. Una compra ya llena o cancelada no mete al ticker
    en el gráfico: el 28/9 las cerradas de la mañana seguían ahí porque
    cualquier orden de hoy contaba como "en operación"."""
    out: list[tuple[str, str]] = []
    vistos: set[str] = set()
    for p in posiciones or []:
        if not isinstance(p, dict):
            continue
        simbolo = p.get("symbol")
        if isinstance(simbolo, str) and simbolo and simbolo not in vistos:
            vistos.add(simbolo)
            out.append((simbolo, "abierta"))
    for o in ordenes_abiertas or []:
        if not isinstance(o, dict) or not (_orden_sigue_viva and _orden_sigue_viva(o)):
            continue
        if str(o.get("side") or "").lower() != "buy":
            continue
        simbolo = o.get("symbol")
        if isinstance(simbolo, str) and simbolo and simbolo not in vistos:
            vistos.add(simbolo)
            out.append((simbolo, "pendiente"))
    return out


def _entrada_watchlist(ticker: str, watch: list[dict]) -> dict | None:
    """La entrada de la watchlist que corresponde a la operación: la más
    reciente del ticker, con preferencia por triggered y luego watching."""
    prioridad = {"triggered": 0, "watching": 1}
    candidatas = [w for w in watch if w.get("ticker") == ticker]
    if not candidatas:
        return None
    minimo = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(candidatas, key=lambda w: (
        prioridad.get(str(w.get("estado") or "").lower(), 2),
        -(w.get("creado_en") or minimo).timestamp()))[0]


def ordenes_enviadas_de_hoy(eventos: list[dict]) -> dict[str, dict]:
    """Último evento `orden` enviada de hoy por ticker. De ahí salen los
    niveles con los que el ejecutor decidió (2026-09-29)."""
    salida: dict[str, dict] = {}
    for e in eventos:
        if e.get("tipo") == "orden" and e.get("estado") == "enviada" and e.get("ticker"):
            salida[str(e["ticker"])] = e
    return salida


def marcas_de(ticker: str, posiciones: list, ordenes: list, watch: list[dict],
              decision: dict | None = None) -> dict:
    """Niveles a dibujar. Cada uno sale de UNA fuente concreta y si falta es
    None, nunca un cálculo:
      ruptura       -> `ruptura_al_decidir` del evento `orden` de hoy: la
                       ruptura con la que el ejecutor decidió. La de la
                       watchlist se sigue refrescando después de la orden
                       (EMA9/VWAP se mueven), así que no sirve para juzgar
                       la compra. Sin evento o sin el campo: None.
      ruptura_actual-> `ultima_zona_entrada_baja` de la entrada de la watchlist
                       (se muestra aparte, rotulada "actual")
      entrada       -> `filled_avg_price` / `filled_at` de la compra llenada
                       (fill real); si no hay compra de hoy, el precio medio de
                       la posición (`avg_entry_price`), sin hora
      stop          -> `stop_price` de la venta tipo stop que
                       `orden_sigue_viva` acepta, la más reciente"""
    w = _entrada_watchlist(ticker, watch)
    ruptura_actual = w.get("ruptura") if w else None
    decision = decision or {}
    ruptura = num(decision.get("ruptura_al_decidir"))
    patron = decision.get("patron_al_decidir")

    compras = [o for o in ordenes if o.get("symbol") == ticker and o.get("side") == "buy"
               and num(o.get("filled_avg_price")) is not None and parse_ts(o.get("filled_at"))]
    compras.sort(key=lambda o: parse_ts(o.get("filled_at")))
    entrada_precio = entrada_hora = None
    if compras:
        entrada_precio = num(compras[-1].get("filled_avg_price"))
        entrada_hora = parse_ts(compras[-1].get("filled_at"))
    else:
        pos = next((p for p in posiciones if p.get("symbol") == ticker), None)
        if pos is not None:
            entrada_precio = num(pos.get("avg_entry_price"))

    stops, objetivos = [], []
    minimo = datetime.min.replace(tzinfo=timezone.utc)
    for o in ordenes:
        # El símbolo de la pata se hereda del padre: Alpaca a veces no lo repite.
        simbolo_padre = o.get("symbol", ticker)
        for candidata in [o, *(o.get("legs") or [])]:
            if not isinstance(candidata, dict) or candidata.get("symbol", simbolo_padre) != ticker:
                continue
            if candidata.get("side") != "sell":
                continue
            if not (_orden_sigue_viva and _orden_sigue_viva(candidata)):
                continue
            momento = parse_ts(candidata.get("submitted_at")) or minimo
            if candidata.get("type") in TIPOS_STOP:
                precio = num(candidata.get("stop_price"))
                if precio is not None:
                    stops.append((momento, precio))
            elif candidata.get("type") == "limit":
                # El take-profit del bracket. Un límite de venta vivo del
                # ticker es el objetivo; si no hay, no se dibuja.
                precio = num(candidata.get("limit_price"))
                if precio is not None:
                    objetivos.append((momento, precio))
    stop = sorted(stops)[-1][1] if stops else None
    objetivo = sorted(objetivos)[-1][1] if objetivos else None
    return {"ruptura": ruptura, "ruptura_actual": ruptura_actual,
            "patron": patron if isinstance(patron, str) and patron else None,
            "vwap_al_decidir": num(decision.get("vwap_al_decidir")),
            "entrada_precio": entrada_precio, "entrada_hora": entrada_hora,
            "stop": stop, "objetivo": objetivo}


# Ventana del gráfico (2026-09-29). Antes se dibujaba desde las 02:00 con
# todo el premarket: en CCL (29/9) el hueco de $21.92 a $25 aplastaba la
# zona donde vive la operación. Se arranca 15 min antes de la apertura
# regular de Nueva York, si con eso quedan velas suficientes; si no (un
# ticker que solo tiene premarket), se deja la serie entera. Es un recorte
# de lo que se ve, no de los datos: el caché guarda la serie completa.
MINUTOS_ANTES_DE_APERTURA = 15
_MINIMO_VELAS_VENTANA = 5
# Hasta cuántos rangos de las velas puede estar un nivel para dibujarse como línea.
_RANGOS_MARCAS = 2.0
# Separación mínima (px del viewBox) entre etiquetas de marcas. El 29/9
# "ruptura", "entrada" y "stop" de CCL quedaban encimadas en el borde.
_SEPARACION_ETIQUETAS = 11.0


def _apertura_ny(momento: datetime) -> datetime:
    local = momento.astimezone(NY)
    return local.replace(hour=9, minute=30, second=0, microsecond=0)


def recortar_a_sesion(velas: dict) -> dict:
    """Las velas desde 15 min antes de la apertura de NY. Si el recorte
    deja menos de 5 velas, o falta una hora, se devuelve la serie tal
    cual: mejor el día entero que un gráfico vacío."""
    marcas = [parse_ts(t) for t in velas.get("timestamps") or []]
    if not marcas or any(m is None for m in marcas):
        return velas
    corte = _apertura_ny(marcas[-1]) - timedelta(minutes=MINUTOS_ANTES_DE_APERTURA)
    idx = next((i for i, m in enumerate(marcas) if m >= corte), None)
    if idx is None or idx == 0 or len(marcas) - idx < _MINIMO_VELAS_VENTANA:
        return velas
    return {k: (v[idx:] if isinstance(v, list) else v) for k, v in velas.items()}


def vwap_de_sesion(velas: dict) -> list[float | None]:
    """VWAP acumulado desde la apertura regular, vela a vela, calculado con
    las MISMAS velas del gráfico (precio típico × volumen). Antes de la
    apertura es None. Si a una vela de la sesión le falta el volumen, el
    VWAP se corta ahí (None de ahí en adelante): un volumen ausente no es
    un cero, y un VWAP con un hueco dentro no es el VWAP."""
    ts = [parse_ts(t) for t in velas.get("timestamps") or []]
    vol = velas.get("volume")
    n = len(velas.get("close") or [])
    if not isinstance(vol, list) or len(vol) != n or len(ts) != n:
        return [None] * n
    out: list[float | None] = []
    suma_pv = suma_v = 0.0
    roto = False
    for i in range(n):
        if ts[i] is None or ts[i] < _apertura_ny(ts[i]):
            out.append(None)
            continue
        v = num(vol[i])
        h, lw, c = num(velas["high"][i]), num(velas["low"][i]), num(velas["close"][i])
        if roto or v is None or v < 0 or None in (h, lw, c):
            roto = True
            out.append(None)
            continue
        suma_pv += (h + lw + c) / 3 * v
        suma_v += v
        out.append(suma_pv / suma_v if suma_v > 0 else None)
    return out


def repartir_etiquetas(ys: list[float], arriba: float, abajo: float,
                       separacion: float = _SEPARACION_ETIQUETAS) -> list[float]:
    """Mueve las etiquetas lo mínimo para que no se encimen, dentro de
    [arriba, abajo]. Devuelve las nuevas y en el MISMO orden de entrada.
    Las líneas se quedan en su precio; solo el texto se desplaza."""
    if not ys:
        return []
    orden = sorted(range(len(ys)), key=lambda i: ys[i])
    pos = [ys[i] for i in orden]
    pos[0] = max(pos[0], arriba)
    for k in range(1, len(pos)):
        pos[k] = max(pos[k], pos[k - 1] + separacion)
    # Si se pasó por abajo, se empuja el bloque hacia arriba.
    if pos[-1] > abajo:
        pos[-1] = abajo
        for k in range(len(pos) - 2, -1, -1):
            pos[k] = min(pos[k], pos[k + 1] - separacion)
    out = [0.0] * len(ys)
    for k, i in enumerate(orden):
        out[i] = pos[k]
    return out


def rango_de_escala(velas: dict) -> tuple[float, float]:
    """(mínimo, máximo) de precio que fija la escala del gráfico.

    Sale de las velas desde la apertura, no del cuarto de hora de
    premarket que también se dibuja. El 29/9 CCL traía en el premarket
    velas cerca de $21.59 contra una sesión de ~$24.5: con ellas en la
    escala, la zona donde vive la operación quedaba aplastada en una
    franja. Con menos de 5 velas de sesión (antes de abrir o recién
    abierto) se usa la serie entera: mejor esa escala que ninguna."""
    ts = [parse_ts(t) for t in velas.get("timestamps") or []]
    sesion = [i for i, m in enumerate(ts) if m is not None and m >= _apertura_ny(m)]
    base = sesion if len(sesion) >= _MINIMO_VELAS_VENTANA else range(len(velas["close"]))
    return min(velas["low"][i] for i in base), max(velas["high"][i] for i in base)


def _grafico_velas(res: dict, marcas: dict, tz, ahora: datetime, clave: str = "") -> str:
    """Velas de 1 min en SVG, sin librerías. Las marcas que faltan no se
    dibujan (el pie del panel dice "sin dato"). Una marca fuera del rango
    de precios de las velas se anota en el borde en vez de aplastar las
    velas para que quepa."""
    # 2026-10-01: canal de 178 px a la derecha para las etiquetas de precio
    # (antes iban encima de las velas y se pisaban con las líneas).
    ancho, alto, x0, x1, y0, y1 = 650, 230, 56, 470, 196, 14
    partes = [f'<svg viewBox="0 0 {ancho} {alto}" role="img" aria-label="Velas de 1 minuto de hoy">',
              f'<line x1="{x0}" y1="{y1}" x2="{x0}" y2="{y0}" class="rejilla"/>',
              f'<line x1="{x0}" y1="{y0}" x2="{x1}" y2="{y0}" class="rejilla"/>']
    velas = res.get("velas")
    if not velas:
        partes.append(f'<text x="{(x0+x1)/2}" y="110" text-anchor="middle" class="eje">Sin datos</text>')
        partes.append(f'<text x="{(x0+x1)/2}" y="128" text-anchor="middle" class="eje">{esc(res.get("error") or "sin velas")}</text>')
        partes.append("</svg>")
        return "".join(partes)

    velas = recortar_a_sesion(velas)
    n = len(velas["close"])
    marcas_ts = [parse_ts(t) for t in velas["timestamps"]]
    minimo, maximo = rango_de_escala(velas)
    rango = maximo - minimo
    if rango < 1e-9:
        rango = max(0.01, minimo * 0.002)
    if minimo < 5 and rango < minimo * 0.02:
        # Acción de menos de $5: con un rango de 1–2 centavos cada vela era
        # una barra suelta. Se da aire a la escala (solo presentación).
        extra = (minimo * 0.02 - rango) / 2
        minimo, maximo = minimo - extra, maximo + extra
        rango = maximo - minimo
    objetivo = marcas.get("objetivo")
    # Una marca a menos de dos rangos de distancia entra al eje; más lejos,
    # se anota en el borde. Eran uno, pero desde que la escala sale solo
    # de la sesión (más ajustada) el objetivo de CCL a 2R quedaba afuera
    # justo cuando es la línea que más interesa ver.
    margen = rango * _RANGOS_MARCAS
    ruptura_actual = marcas.get("ruptura_actual")
    if ruptura_actual is not None and marcas["ruptura"] is not None and abs(ruptura_actual - marcas["ruptura"]) < 0.005:
        ruptura_actual = None   # la misma: una sola línea
    dentro = [v for v in (marcas["ruptura"], ruptura_actual, marcas["entrada_precio"], marcas["stop"], objetivo)
              if v is not None and minimo - margen <= v <= maximo + margen]
    lo = min([minimo, *dentro]) - rango * 0.08
    hi = max([maximo, *dentro]) + rango * 0.08

    def y(v: float) -> float:
        return y0 - (v - lo) / (hi - lo) * (y0 - y1)

    paso = (x1 - x0) / n
    cuerpo = max(1.0, min(6.0, paso * 0.7))

    def x(i: int) -> float:
        return x0 + paso * (i + 0.5)

    for valor in (minimo, maximo):
        partes.append(f'<text x="{x0-6}" y="{y(valor)+4:.1f}" text-anchor="end" class="eje">{esc(fmt_dinero(valor))}</text>')
    # Una vela de premarket fuera de la escala se recorta al área del
    # gráfico (no se estira ni se mueve: lo que se ve es su tramo real
    # dentro de la escala) y la nota de abajo dice a qué precio llegó.
    id_recorte = "recorte-" + re.sub(r"[^A-Za-z0-9_-]", "", clave or str(id(res)))
    partes.append(f'<clipPath id="{id_recorte}"><rect x="{x0}" y="{y1}" width="{x1-x0}" height="{y0-y1}"/></clipPath>')
    partes.append(f'<g clip-path="url(#{id_recorte})">')
    for i in range(n):
        o, c, h, lw = velas["open"][i], velas["close"][i], velas["high"][i], velas["low"][i]
        cls = "vela-sube" if c >= o else "vela-baja"
        partes.append(f'<line class="{cls}" x1="{x(i):.1f}" y1="{y(h):.1f}" x2="{x(i):.1f}" y2="{y(lw):.1f}" stroke-width="1"/>')
        top, base = max(o, c), min(o, c)
        partes.append(f'<rect class="vela {cls}" x="{x(i)-cuerpo/2:.1f}" y="{y(top):.1f}" width="{cuerpo:.1f}" '
                      f'height="{max(1.0, y(base)-y(top)):.1f}"/>')
    partes.append("</g>")
    bajas = [velas["low"][i] for i in range(n) if velas["low"][i] < lo]
    altas = [velas["high"][i] for i in range(n) if velas["high"][i] > hi]
    if bajas or altas:
        extremos = ([f"mín {fmt_dinero(min(bajas))}"] if bajas else []) + ([f"máx {fmt_dinero(max(altas))}"] if altas else [])
        # Abajo, entre las dos horas del eje: ahí no tapa ninguna vela.
        partes.append(f'<text class="eje nota-escala" x="{(x0+x1)/2:.0f}" y="{alto-6}" text-anchor="middle">'
                      f'premarket fuera de escala: {esc(" · ".join(extremos))}</text>')

    # VWAP: línea continua fina, solo donde hay dato.
    vwap = vwap_de_sesion(velas)
    tramo: list[str] = []
    tramos: list[list[str]] = []
    for i, v in enumerate(vwap):
        if v is None:
            if tramo:
                tramos.append(tramo)
            tramo = []
            continue
        tramo.append(f"{x(i):.1f},{y(v):.1f}")
    if tramo:
        tramos.append(tramo)
    for t in tramos:
        if len(t) > 1:
            partes.append(f'<polyline class="marca-vwap m-vwap" fill="none" stroke-width="1.2" points="{" ".join(t)}"/>')

    etiquetas: list[tuple[float, str]] = []   # (y deseada, svg sin la y)

    def marca_horizontal(valor, nombre, clase, dash, texto=None):
        if valor is None:
            return
        rotulo = texto or nombre
        if lo <= valor <= hi:
            yv = y(valor)
            partes.append(f'<line class="marca-{nombre} {clase}" x1="{x0}" y1="{yv:.1f}" x2="{x1}" y2="{yv:.1f}" stroke-width="1.2" stroke-dasharray="{dash}"/>')
            etiquetas.append((yv + 3, f'text-anchor="start" class="eje {clase}-txt">{esc(rotulo)} {esc(fmt_dinero(valor))}'))
        else:
            yv = y1 + 10 if valor > hi else y0 - 6
            etiquetas.append((yv, f'text-anchor="start" class="eje marca-{nombre}-fuera {clase}-txt">{esc(rotulo)} {esc(fmt_dinero(valor))} (fuera)'))

    marca_horizontal(objetivo, "objetivo", "m-objetivo", "8 3")
    marca_horizontal(marcas["ruptura"], "ruptura", "m-ruptura", "6 4", texto="ruptura al decidir")
    marca_horizontal(ruptura_actual, "ruptura-actual", "m-ruptura-actual", "2 4", texto="ruptura actual")
    marca_horizontal(marcas["stop"], "stop", "m-stop", "3 3")
    marca_horizontal(marcas["entrada_precio"], "entrada", "m-entrada", "1 3")
    ultimo_vwap = next((v for v in reversed(vwap) if v is not None), None)
    if ultimo_vwap is not None:
        etiquetas.append((y(ultimo_vwap) + 3, f'text-anchor="start" class="eje m-vwap-txt">VWAP {esc(fmt_dinero(ultimo_vwap))}'))
    nuevas = repartir_etiquetas([e[0] for e in etiquetas], y1 + 8, y0 - 3, separacion=12.0)
    for (y_linea, cuerpo_txt), yv in zip(etiquetas, nuevas):
        # Etiqueta en el canal derecho; si se desplazó, un trazo fino la
        # une con su línea.
        if abs(yv - y_linea) > 1:
            partes.append(f'<line class="rejilla" x1="{x1}" y1="{y_linea - 3:.1f}" x2="{x1 + 4}" y2="{yv - 3:.1f}" stroke-width="0.8"/>')
        partes.append(f'<text x="{x1 + 6}" y="{yv:.1f}" {cuerpo_txt}</text>')

    hora = marcas["entrada_hora"]
    if hora is not None and marcas_ts and marcas_ts[0] is not None:
        # Vela más cercana al fill real (sin interpolar entre velas).
        idx = min(range(n), key=lambda i: abs((marcas_ts[i] - hora).total_seconds()) if marcas_ts[i] else float("inf"))
        if marcas_ts[idx] is not None and abs((marcas_ts[idx] - hora).total_seconds()) <= 120:
            partes.append(f'<line class="marca-entrada-hora m-entrada" x1="{x(idx):.1f}" y1="{y1}" x2="{x(idx):.1f}" y2="{y0}" stroke-width="1" stroke-dasharray="2 3"/>')
            if marcas["entrada_precio"] is not None and lo <= marcas["entrada_precio"] <= hi:
                partes.append(f'<circle cx="{x(idx):.1f}" cy="{y(marcas["entrada_precio"]):.1f}" r="3.5" class="ink"/>')
    if marcas_ts[0] is not None:
        partes.append(f'<text x="{x0}" y="{alto-6}" class="eje">{esc(_hora(marcas_ts[0], tz, ahora=ahora))}</text>')
    if n > 1 and marcas_ts[-1] is not None:
        partes.append(f'<text x="{x1}" y="{alto-6}" text-anchor="end" class="eje">{esc(_hora(marcas_ts[-1], tz, ahora=ahora))}</text>')
    partes.append("</svg>")
    return "".join(partes)


def _pie_marcas(marcas: dict, tz, ahora: datetime) -> str:
    def dinero(v):
        return fmt_dinero(v) if v is not None else "sin dato"
    entrada = dinero(marcas["entrada_precio"])
    if marcas["entrada_precio"] is not None:
        # "· 10:18" y no "a las 10:18": con cuatro columnas el texto largo partía la línea.
        hora_entrada = (f"a las {_hora(marcas['entrada_hora'], tz, ahora=ahora)}" if marcas["entrada_hora"]
                        else "hora sin dato")
    else:
        hora_entrada = ""
    actual = marcas.get("ruptura_actual")
    sub_actual = (f'<span class="mono sub-nivel">actual {esc(fmt_dinero(actual))}</span>'
                  if actual is not None else '<span class="mono sub-nivel">actual sin dato</span>')
    contexto = []
    if marcas.get("patron"):
        contexto.append(f"patrón {marcas['patron']}")
    if marcas.get("vwap_al_decidir") is not None:
        contexto.append(f"VWAP al decidir {fmt_dinero(marcas['vwap_al_decidir'])}")
    nota = f'<div class="mono sub-nivel">Al decidir: {esc(" · ".join(contexto))}</div>' if contexto else ""
    return (f'<div class="stats s4"><div><span class="mono">Ruptura al decidir</span><b>{esc(dinero(marcas["ruptura"]))}</b>{sub_actual}</div>'
            f'<div><span class="mono">Entrada</span><b>{esc(entrada)}</b>'
            + (f'<span class="mono sub-nivel">{esc(hora_entrada)}</span>' if hora_entrada else "") + '</div>'
            f'<div><span class="mono">Stop</span><b>{esc(dinero(marcas["stop"]))}</b></div>'
            f'<div><span class="mono">Objetivo</span><b>{esc(dinero(marcas.get("objetivo")))}</b></div></div>{nota}')


def _marca_fuente_velas(origen_fuente) -> str | None:
    """Lo que se lee en el gráfico. `alpaca-sip` es SIP; el respaldo se
    nombra entero para que no se confunda con una vela del feed. Otro
    feed (iex) se muestra tal cual, no disfrazado de SIP."""
    if origen_fuente == "yahoo (respaldo)":
        return "Yahoo (respaldo)"
    if origen_fuente == "yahoo":
        # Copias en caché de cuando el plan gratis no daba SIP en vivo
        # (29/9–1/10): ahí Yahoo era la fuente principal.
        return "Yahoo"
    if origen_fuente == "alpaca-sip":
        return "SIP"
    if isinstance(origen_fuente, str) and origen_fuente.startswith("alpaca-") and len(origen_fuente) > len("alpaca-"):
        return origen_fuente.split("-", 1)[1].upper()
    return None


def _subtitulo_velas(res: dict, tz, ahora: datetime) -> str:
    velas = res.get("velas")
    if not velas:
        return "Sin datos"
    frescura = {"fuente": "Yahoo", "cache": "caché", "cache vencida": "caché vencida"}.get(res.get("origen"), "—")
    hora = _hora(res.get("obtenido"), tz, ahora=ahora)
    n = len(velas["close"])
    marca = _marca_fuente_velas(res.get("origen_fuente"))
    # Sin marca (copia vieja de antes de anotar la fuente, o un doble de
    # prueba): se conserva el texto de siempre.
    if marca is None:
        return f"{n} velas · {frescura} {hora}"
    if res.get("origen") == "fuente":
        return f"{n} velas · {marca} {hora}"
    return f"{n} velas · {frescura} {hora} · {marca}"


# ───────────────────────── render ─────────────────────────

def esc(v) -> str:
    return html.escape("—" if v is None else str(v))


DIAS_ES = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
MESES_ES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def _dia(d_local: datetime, hoy_local: datetime) -> str | None:
    """None si es hoy; "vie" si es de los últimos 6 días; "18 sep" si no.
    Así una hora vieja nunca se confunde con una de hoy."""
    dias = (hoy_local.date() - d_local.date()).days
    if dias == 0:
        return None
    if 0 < dias < 7:
        return DIAS_ES[d_local.weekday()]
    return f"{d_local.day} {MESES_ES[d_local.month - 1]}"


def _hora(d: datetime | None, tz, segundos: bool = False, ahora: datetime | None = None) -> str:
    if d is None:
        return "—"
    local = d.astimezone(tz)
    hora = local.strftime("%H:%M:%S" if segundos else "%H:%M")
    dia = _dia(local, ahora.astimezone(tz)) if ahora is not None else None
    return f"{dia} {hora}" if dia else hora


def _cuando(d: datetime | None, tz, ahora: datetime) -> str:
    """"de las 14:40" si es de hoy, "del vie 22:33" si no, "—" sin dato."""
    if d is None:
        return "—"
    texto = _hora(d, tz, ahora=ahora)
    return f"del {texto}" if " " in texto else f"de las {texto}"


def fmt_dinero(v: float | None, signo: bool = False) -> str:
    if v is None:
        return "sin dato"
    cuerpo = f"${abs(v):,.2f}"
    if signo:
        return ("+" if v >= 0 else "−") + cuerpo
    return ("−" if v < 0 else "") + cuerpo


def fmt_num(v, sufijo: str = "") -> str:
    if v is None:
        return "sin dato"
    texto = f"{v:.1f}" if isinstance(v, float) and not v.is_integer() else f"{int(v)}"
    return texto + sufijo


def _veredicto_latencia(ctx: dict) -> str:
    """Una frase en lenguaje llano. Solo lee lo ya calculado; no cambia la medida."""
    if ctx["lat_mediana"] is None:
        return ""
    fuera = ctx["lat_fuera"] or 0
    sin_dato = len(ctx.get("lat_sin_barra") or [])
    if fuera == 0:
        # Una compra sin dato no se cuenta como "a tiempo" (regla 6).
        if sin_dato:
            con_dato = len(ctx.get("lat") or [])
            sujeto = ("la compra con dato salió" if con_dato == 1
                      else f"las {con_dato} compras con dato salieron")
            return f'<div class="nota-info">Bien: {sujeto} dentro del límite ({sin_dato} sin dato).</div>'
        return '<div class="nota-info">Bien: todas las compras de hoy salieron dentro del límite.</div>'
    compras = "compra llegó" if fuera == 1 else "compras llegaron"
    return f'<div class="nota">Ojo: {fuera} {compras} tarde hoy (más de {esc(fmt_num(ctx["presupuesto"]))} min).</div>'


def _grafico_latencia(ctx: dict) -> str:
    """Una barra por compra: abajo la reacción del bot (roja si pasó el
    límite), encima la espera por cupo lleno en gris. El eje va de 0 al
    doble del límite; una barra más alta se corta arriba con su total
    escrito, en vez de aplastar las demás (el 29/9, una espera de 88 min
    dejaba las etiquetas 0-88 amontonadas y la de "8 min" tapada)."""
    ancho, alto, x0, y0, y1 = 480, 230, 36, 196, 18
    presupuesto = ctx["presupuesto"]
    detalle = (ctx.get("lat_detalle") or [])[-40:]
    tope = max(12.0, presupuesto * 2)
    y = lambda v: y0 - (min(v, tope) / tope) * (y0 - y1)
    partes = [
        f'<svg viewBox="0 0 {ancho} {alto}" role="img" aria-label="Minutos entre la señal de ruptura y la compra, una barra por compra">',
        f'<rect x="{x0+1}" y="{y1}" width="{ancho-x0-10}" height="{y(presupuesto)-y1:.1f}" class="zona-riesgo"/>',
        f'<line x1="{x0}" y1="{y1}" x2="{x0}" y2="{y0}" class="rejilla"/>',
        f'<line x1="{x0}" y1="{y0}" x2="{ancho-10}" y2="{y0}" class="rejilla"/>',
    ]
    for marca in sorted({0.0, presupuesto, tope}):
        partes.append(f'<text x="{x0-8}" y="{y(marca)+4:.1f}" text-anchor="end" class="eje">{fmt_num(marca)}</text>')
    yp = y(presupuesto)
    partes.append(f'<line x1="{x0}" y1="{yp:.1f}" x2="{ancho-10}" y2="{yp:.1f}" class="limite" stroke-width="1.5" stroke-dasharray="6 4"/>')
    if detalle:
        paso = (ancho - x0 - 20) / len(detalle)
        barra = max(3.0, min(28.0, paso * 0.7))
        for i, d in enumerate(detalle):
            xb = x0 + 6 + i * paso + (paso * 0.7 - barra) / 2
            reaccion, total = d["reaccion"], d["total"]
            cls = "barra-alta" if reaccion > presupuesto else "barra-ok"
            partes.append(f'<rect class="{cls}" x="{xb:.1f}" y="{y(reaccion):.1f}" width="{barra:.1f}" height="{y0-y(reaccion):.1f}"/>')
            if d["espera"] > 0:
                partes.append(f'<rect class="barra-espera" x="{xb:.1f}" y="{y(total):.1f}" width="{barra:.1f}" '
                              f'height="{y(reaccion)-y(total):.1f}"><title>{esc(d["ticker"])}: '
                              f'{esc(fmt_num(d["espera"]))} min esperando cupo</title></rect>')
            if total > tope:
                # Cortada: una muesca y el total real encima.
                partes.append(f'<text x="{xb+barra/2:.1f}" y="{y1-5}" text-anchor="middle" class="eje valor-cortado">'
                              f'▲ {esc(fmt_num(total))}</text>')
            if len(detalle) <= 12:
                partes.append(f'<text x="{xb+barra/2:.1f}" y="{y0+13}" text-anchor="middle" class="eje">{esc(d["ticker"])}</text>')
    else:
        mensaje = "Todavía no hay compras hoy con el dato completo."
        partes.append(f'<text x="{(ancho+x0)/2}" y="120" text-anchor="middle" class="eje">{esc(mensaje)}</text>')
    # La etiqueta del límite va al final para que ninguna barra la tape.
    partes.append(f'<text x="{x0+4}" y="{yp-6:.1f}" class="eje rojo etiqueta-limite">límite {fmt_num(presupuesto)} min</text>')
    partes.append("</svg>")
    return "".join(partes)


def _html_notas_latencia(ctx: dict) -> str:
    partes = []
    esperas = [d for d in ctx.get("lat_detalle") or [] if d["espera"] > 0]
    if esperas:
        items = " · ".join(f"{d['ticker']} {fmt_num(d['espera'])} min" for d in esperas)
        partes.append(f'<div class="nota-info">Esperando cupo (no cuenta como tarde): {esc(items)}.</div>')
    sin_barra = ctx.get("lat_sin_barra") or []
    if sin_barra:
        items = " · ".join(f"{d['ticker']} ({d['motivo']})" for d in sin_barra)
        partes.append(f'<div class="nota">Compras sin barra: {esc(items)}.</div>')
    return "".join(partes)


CSS = """
:root{--fondo:#f3f1ea;--papel:#fff;--tinta:#16171a;--gris:#5c5b55;--gris2:#45443f;--linea:#d6d3c8;--linea2:#ebe8df;
--acento:#2451b8;--verde:#1f7a4d;--rojo:#b3261e;--mono:'JetBrains Mono',ui-monospace,monospace;
--rejilla:#bdb9ad;--zona-riesgo:#fbeceb;--ok-bg:#e6f1ea;--ok-fg:#1b6a43;--mal-bg:#fbeceb;--mal-fg:#8f1d17;--duda-bg:#f6f4ee;
--oscuro-bg:var(--tinta);--oscuro-fg:#e9e7df;--oscuro-linea:#2c2d31;--oscuro-tk:#fff;--oscuro-sub:#a9a69b;--oscuro-td:#c9c6bb}
/* Tema oscuro: por preferencia del sistema (salvo que el usuario haya
   elegido claro a mano) y por elección manual guardada en el navegador.
   Se redefinen las MISMAS variables; el resto del CSS ya las usa, así que
   el modo claro queda idéntico. */
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--fondo:#000000;--papel:#1b1c20;--tinta:#e9e7df;--gris:#9a978d;--gris2:#c7c4ba;--linea:#2f3034;--linea2:#26272b;--acento:#7a9bff;--verde:#46b37e;--rojo:#f2685e;--rejilla:#3a3b40;--zona-riesgo:#37211f;--ok-bg:#15352a;--ok-fg:#7fd0a4;--mal-bg:#37211f;--mal-fg:#f0a49d;--duda-bg:#24252a;--oscuro-bg:#16171b;--oscuro-fg:#e9e7df;--oscuro-linea:#2c2d31;--oscuro-tk:#fff;--oscuro-sub:#a9a69b;--oscuro-td:#c9c6bb}}
:root[data-theme="dark"]{--fondo:#000000;--papel:#1b1c20;--tinta:#e9e7df;--gris:#9a978d;--gris2:#c7c4ba;--linea:#2f3034;--linea2:#26272b;--acento:#7a9bff;--verde:#46b37e;--rojo:#f2685e;--rejilla:#3a3b40;--zona-riesgo:#37211f;--ok-bg:#15352a;--ok-fg:#7fd0a4;--mal-bg:#37211f;--mal-fg:#f0a49d;--duda-bg:#24252a;--oscuro-bg:#16171b;--oscuro-fg:#e9e7df;--oscuro-linea:#2c2d31;--oscuro-tk:#fff;--oscuro-sub:#a9a69b;--oscuro-td:#c9c6bb}
*{box-sizing:border-box}
body{margin:0;background:var(--fondo);color:var(--tinta);font-family:'Space Grotesk','Helvetica Neue',sans-serif}
main{max-width:1440px;margin:0 auto;padding:32px 40px;display:flex;flex-direction:column;gap:20px}
header{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;padding-bottom:20px;border-bottom:1px solid var(--linea)}
.marca{display:flex;align-items:center;gap:16px}
.logo{width:48px;height:48px;background:var(--acento);border-radius:6px;display:grid;place-items:center}
h1{margin:0;font-size:28px;letter-spacing:.04em}
.sub,.mono{font-family:var(--mono);font-size:12px;color:var(--gris)}
.pildoras{display:flex;gap:10px;flex-wrap:wrap;font-family:var(--mono);font-size:12px}
.pildora{padding:8px 12px;border-radius:4px;background:var(--papel);border:1px solid var(--linea)}
.pildora.paper{border:1.5px solid var(--acento);color:var(--acento);font-weight:700}
.pildora.ok{background:var(--ok-bg);color:var(--ok-fg);border-color:transparent}
.pildora.mal{background:var(--mal-bg);color:var(--mal-fg);border-color:transparent}
button.pildora{cursor:pointer;font-family:var(--mono);font-size:12px;line-height:1.2;color:inherit}
button.pildora:hover{border-color:var(--acento)}
.problemas{background:var(--mal-bg);color:var(--mal-fg);border-radius:6px;padding:14px 18px;font-size:14px}
.problemas ul{margin:6px 0 0;padding-left:18px}
.fila{display:grid;gap:12px}
.c4{grid-template-columns:repeat(4,minmax(0,1fr))}.c5{grid-template-columns:repeat(5,minmax(0,1fr))}
.c3{grid-template-columns:repeat(3,minmax(0,1fr))}.c2{grid-template-columns:1.55fr 1fr}
.c2i{grid-template-columns:repeat(2,minmax(0,1fr))}
.panel{background:var(--papel);border:1px solid var(--linea);border-radius:6px;padding:18px;display:flex;flex-direction:column;gap:10px;min-width:0}
.panel.oscuro{background:var(--oscuro-bg);color:var(--oscuro-fg);border-color:var(--oscuro-bg)}
.titulo{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.titulo h2{margin:0;font-size:16px;letter-spacing:.04em}
.etapa .nombre{font-size:20px;font-weight:700}.etapa .rol{font-size:13px;color:var(--gris2)}
.etapa .pie{display:flex;justify-content:space-between;padding-top:10px;border-top:1px dashed var(--linea)}
.punto{display:inline-flex;align-items:center;gap:6px;font-family:var(--mono);font-size:11px}
.punto::before{content:"";width:8px;height:8px;border-radius:50%;background:currentColor}
.ok{color:var(--verde)}.alerta{color:var(--rojo)}.sin-datos{color:var(--gris)}.info{color:var(--gris2)}
.kpi .valor{font-family:var(--mono);font-size:30px;font-weight:500}
.pos{color:var(--verde)}.neg{color:var(--rojo)}
table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:13px}
th{text-align:left;font-weight:400;font-size:11px;color:var(--gris);padding:8px 8px 8px 0;border-bottom:1px solid var(--linea)}
td{padding:8px 8px 8px 0;border-bottom:1px solid var(--linea2);color:var(--gris2)}
td.tk{color:var(--tinta);font-weight:700}
.oscuro td{border-color:var(--oscuro-linea);color:var(--oscuro-td)}.oscuro td.tk{color:var(--oscuro-tk)}.oscuro .sub{color:var(--oscuro-sub)}
.vacio{font-size:13px;color:var(--gris);padding:12px 0}
.oscuro .vacio{color:var(--oscuro-sub)}
.duda{background:var(--duda-bg);border-radius:4px;padding:10px 12px;display:flex;flex-direction:column;gap:4px}
.duda .cab{display:flex;justify-content:space-between;font-family:var(--mono);font-size:12px}
.duda p{margin:0;font-size:13px;color:var(--gris2)}
.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;font-family:var(--mono)}
.stats b{display:block;font-size:18px;font-weight:500}
.stats.s4{grid-template-columns:repeat(4,minmax(0,1fr))}
@media (max-width:640px){.stats.s4{grid-template-columns:repeat(2,minmax(0,1fr))}}
svg{width:100%;height:auto}.eje{font-family:var(--mono);font-size:10px;fill:var(--gris)}.eje.rojo{fill:var(--rojo)}
.rejilla{stroke:var(--rejilla)}.ink{fill:var(--tinta)}.zona-riesgo{fill:var(--zona-riesgo)}
.serie{stroke:var(--acento)}.serie-vivo{stroke:var(--acento)}.serie-punto{fill:var(--acento)}.base-punteada{stroke:var(--gris)}
.vela-sube{fill:var(--verde);stroke:var(--verde)}.vela-baja{fill:var(--rojo);stroke:var(--rojo)}
.m-ruptura{stroke:var(--acento)}.m-ruptura-txt{fill:var(--acento)}
.m-stop{stroke:var(--rojo)}.m-stop-txt{fill:var(--rojo)}
.m-entrada{stroke:var(--gris)}.m-entrada-txt{fill:var(--gris)}
.m-objetivo{stroke:var(--verde)}.m-objetivo-txt{fill:var(--verde)}
.m-ruptura-actual{stroke:var(--gris)}.m-ruptura-actual-txt{fill:var(--gris)}
.sub-nivel{display:block;font-size:11px;color:var(--gris)}
.historico{font-size:11px;color:var(--gris)}
.m-vwap{stroke:#b7791f}.m-vwap-txt{fill:#b7791f}
.m-ruptura-txt,.m-ruptura-actual-txt,.m-stop-txt,.m-entrada-txt,.m-objetivo-txt,.m-vwap-txt,.nota-escala{paint-order:stroke;stroke:var(--papel);stroke-width:3px;stroke-linejoin:round}
.barra-ok{fill:var(--acento)}.barra-alta{fill:var(--rojo)}.limite{stroke:var(--rojo)}.barra-espera{fill:var(--rejilla)}.valor-cortado{font-weight:700}.etiqueta-limite{paint-order:stroke;stroke:var(--papel);stroke-width:3px;stroke-linejoin:round}
.explica{margin:0 0 8px;font-size:13px;line-height:1.45;color:var(--gris2)}
.nota{margin-top:auto;padding:10px 12px;background:var(--mal-bg);border-radius:4px;font-family:var(--mono);font-size:12px;color:var(--mal-fg)}
.nota-info{margin-top:auto;padding:10px 12px;background:var(--duda-bg);border-radius:4px;font-family:var(--mono);font-size:12px;color:var(--gris2)}
/* Historial del día: ya no está pasando. Gris, nunca el rojo de Revisar. */
.nota-historial{margin-top:auto;padding:10px 12px;border-radius:4px;font-family:var(--mono);font-size:12px;color:var(--gris)}
tr.historial td,tr.historial td.tk,h3.historial{color:var(--gris);font-weight:400}
h3{margin:12px 0 0;font-size:13px;letter-spacing:.04em;font-weight:500}
.badge{font-family:var(--mono);font-size:11px;padding:2px 8px;border-radius:99px;border:1px solid var(--acento);color:var(--acento)}
.scroll{overflow-x:auto}
.anclas{position:sticky;top:0;z-index:5;display:flex;flex-wrap:wrap;gap:6px;padding:8px 0;background:var(--fondo);border-bottom:1px solid var(--linea);font-family:var(--mono);font-size:12px}
.anclas a,.anclas-movil a{color:var(--tinta);text-decoration:none;padding:6px 10px;border-radius:4px;border:1px solid var(--linea);background:var(--papel)}
.anclas a:hover,.anclas-movil a:hover{border-color:var(--acento);color:var(--acento)}
.anclas-movil{display:none;position:sticky;top:0;z-index:5;background:var(--fondo);font-family:var(--mono);font-size:13px}
.anclas-movil>summary{cursor:pointer;padding:10px 12px;border:1px solid var(--linea);border-radius:4px;background:var(--papel);list-style:none}
.anclas-movil nav{display:flex;flex-wrap:wrap;gap:6px;padding:8px 0}
main>[id]{scroll-margin-top:56px}
details.sistema{background:var(--papel);border:1px solid var(--linea);border-radius:6px}
details.sistema>summary{cursor:pointer;padding:10px 14px;display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;list-style:none}
details.sistema>summary::-webkit-details-marker{display:none}
details.sistema[open]>section{padding:0 12px 12px}
.fila.c2,.fila.c3{align-items:start}
details.explica-mas>summary{cursor:pointer;padding:2px 0 6px;color:var(--gris)}
@media (max-width:640px){.anclas{display:none}.anclas-movil{display:block}}
table{font-variant-numeric:tabular-nums}
table.n2 td:nth-child(2),table.n2 th:nth-child(2){text-align:right}table.n3 td:nth-child(3),table.n3 th:nth-child(3){text-align:right}table.n4 td:nth-child(4),table.n4 th:nth-child(4){text-align:right}table.n5 td:nth-child(5),table.n5 th:nth-child(5){text-align:right}table.n6 td:nth-child(6),table.n6 th:nth-child(6){text-align:right}table.n7 td:nth-child(7),table.n7 th:nth-child(7){text-align:right}table.n8 td:nth-child(8),table.n8 th:nth-child(8){text-align:right}table.n9 td:nth-child(9),table.n9 th:nth-child(9){text-align:right}
.tabla-watch td.cat span{display:block;max-width:560px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tabla-watch td.cat:hover span,.tabla-watch td.cat:focus span{white-space:normal}
.tabla-watch td.cat{white-space:normal}
.dot{display:none;width:8px;height:8px;border-radius:50%;margin-right:6px;background:var(--gris);vertical-align:middle}.dot.est-triggered{background:var(--acento)}
@media (max-width:640px){.tabla-watch .col-estado{display:none}.dot{display:inline-block}.tabla-watch td.cat span{max-width:170px}}
details.mas{position:relative}details.mas>summary{list-style:none;cursor:pointer}details.mas>summary::-webkit-details-marker{display:none}
details.mas[open]>.pildoras{position:absolute;right:0;top:calc(100% + 6px);z-index:6;background:var(--fondo);padding:8px;border:1px solid var(--linea);border-radius:6px;width:max-content;max-width:90vw}
a.pildora{color:inherit;text-decoration:none}a.pildora:hover{border-color:var(--acento)}
.vacio.falta{color:#b7791f}
.banda-datos{margin-top:0}
details.vela-op>.panel{border:0;padding:6px 12px 12px}
details.vela-op{align-self:start}details.vela-op[open]{grid-column:1/-1}
details.vela-op[open]>.panel{max-width:820px}
@media (max-width:640px){header .pildoras{gap:6px}.pildora{padding:6px 9px}}
.ctl-resumen{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.ctl-card{border:1px solid var(--linea);border-radius:6px;padding:12px 14px;display:flex;flex-direction:column;gap:4px;min-width:0}
.ctl-card b{font-family:var(--mono);font-size:20px;font-weight:500;overflow-wrap:anywhere}
.ctl-card.aviso{border-color:#b7791f}.ctl-card.mal{border-color:var(--rojo);background:var(--mal-bg)}
.ctl-sub{font-family:var(--mono);font-size:11px;color:var(--gris2);overflow-wrap:anywhere}
details.ctl{border:1px solid var(--linea2);border-radius:6px;min-width:0}
details.ctl>summary{cursor:pointer;padding:10px 12px;display:flex;flex-wrap:wrap;gap:4px 14px;align-items:baseline;list-style:none}
details.ctl>summary::-webkit-details-marker{display:none}
details.ctl>summary::before{content:"▸";color:var(--gris);font-size:12px}
details.ctl[open]>summary::before{content:"▾"}
details.ctl>summary b{font-size:14px}
.ctl-cuerpo{padding:0 12px 12px;display:flex;flex-direction:column;gap:10px;min-width:0}
.tabla-ctl{max-width:100%;max-height:340px;overflow:auto;border:1px solid var(--linea2);border-radius:4px}
.tabla-ctl table{min-width:100%;width:max-content}
.tabla-ctl th{position:sticky;top:0;background:var(--papel);z-index:1}
.tabla-ctl th,.tabla-ctl td{white-space:nowrap;padding:6px 12px 6px 8px}
.tabla-ctl td.txt{white-space:normal;min-width:220px;max-width:420px}
.nota-info,.nota,.nota-historial{overflow-wrap:anywhere}
@media (max-width:1100px){.ctl-resumen{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:480px){.ctl-resumen{grid-template-columns:1fr}main{padding:16px 12px}}
details.terminales summary{cursor:pointer;padding:8px 0}details.terminales td{color:var(--gris)}
.est-triggered{color:var(--acento);font-weight:700}
.usos{display:flex;flex-direction:column;gap:10px;padding-bottom:6px;border-bottom:1px dashed var(--linea)}
.uso{display:flex;flex-direction:column;gap:3px}.uso b{font-family:var(--mono);font-size:13px;font-weight:500}
.barra-uso{height:6px;background:var(--linea2);border-radius:3px;overflow:hidden}
.barra-uso div{height:100%;background:var(--acento)}.barra-uso div.alto{background:#b7791f}.barra-uso div.lleno{background:var(--rojo)}
.operaciones{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
@media (max-width:900px){.operaciones{grid-template-columns:1fr}}
@media (max-width:1100px){.c5{grid-template-columns:repeat(3,minmax(0,1fr))}.c4{grid-template-columns:repeat(2,minmax(0,1fr))}.c2,.c3{grid-template-columns:1fr}}
@media (max-width:900px){.c2i{grid-template-columns:1fr}}
@media (max-width:640px){main{padding:20px 16px}.c4,.c5{grid-template-columns:1fr 1fr}.kpi .valor{font-size:22px}}
"""


def fmt_qty(v) -> str:
    n = num(v) if not isinstance(v, (int, float)) else (v if math.isfinite(v) else None)
    if n is None:
        return "—"
    if abs(n - round(n)) < 1e-9:
        return str(int(round(n)))
    return f"{n:.4f}".rstrip("0").rstrip(".")


def _html_pnl(pnl, pct=None) -> str:
    if pnl is None:
        return "—"
    clase = "pos" if pnl >= 0 else "neg"
    extra = "" if pct is None else f" ({pct:+.2f}%)"
    return f'<span class="{clase}">{esc(fmt_dinero(pnl, signo=True) + extra)}</span>'


# Qué significa `held` depende de dónde está la pata (2026-09-29). En una
# posición ya llena, el stop `held` es la mitad OCO del bracket: Alpaca lo
# vigila y se dispara solo, o sea que SÍ protege. En una compra pendiente,
# las patas esperan a que llene la entrada. "held" a secas no decía
# ninguna de las dos cosas.
TEXTO_HELD = {"posicion": "activo (con objetivo)", "pendiente": "tras el fill"}


def _html_nivel(nivel, mercado: bool = False, conocido: bool = True, rol: str = "posicion") -> str:
    if not conocido:
        return "sin datos"
    if mercado and (not nivel or nivel.get("precio") is None):
        return "venta a mercado"
    if not nivel or nivel.get("precio") is None:
        return "—"
    texto = esc(fmt_dinero(nivel["precio"]))
    if nivel.get("estado") == "held":
        texto += (f' <span class="mono" title="Alpaca: held · OCO (stop y objetivo enlazados: si se toca uno, se cancela el otro)">'
                  f'· {esc(TEXTO_HELD.get(rol, "held"))}</span>')
    return texto


def _html_riesgo_fila(f: dict, equity: float | None) -> str:
    # Órdenes del símbolo ilegibles: no se sabe el stop, así que tampoco
    # el riesgo. "—" se leería como "no hay".
    if f.get("salidas_conocidas") is False:
        return "sin datos"
    r = f.get("riesgo")
    if r is None:
        return "—"
    if r <= 0:
        return f'<span class="pos">asegura {esc(fmt_dinero(-r, signo=True))}</span>'
    pct = f" ({r / equity * 100:.2f}%)" if equity else ""
    return esc(fmt_dinero(r) + pct)


def _html_rr(f: dict) -> str:
    if f.get("salidas_conocidas") is False:
        return "sin datos"
    rr = f.get("rr")
    return "—" if rr is None else esc(f"{rr:.1f}R")


def _html_espera(colocada, ahora: datetime, maximo) -> str:
    """Minutos que lleva la compra sin llenar, contra el tope después del
    cual el seguimiento la cancela. Sin hora, "—"."""
    if colocada is None:
        return "—"
    minutos = max(0.0, (ahora - colocada).total_seconds() / 60)
    if maximo is None:
        return esc(f"{minutos:.0f} min")
    clase = ' class="neg"' if minutos > maximo else ""
    return f"<span{clase}>{esc(f'{minutos:.0f} / {maximo:.0f} min')}</span>"


def _frase_aviso(a: dict) -> str:
    """Las mismas frases que `reconciliacion._frase`, para que el panel
    y el Telegram digan lo mismo."""
    partes = []
    if a.get("sin_seguimiento"):
        partes.append("no hay una revisión viva que la siga")
    if a.get("sin_stop"):
        partes.append("no tiene stop de venta abierto")
    return f"{a.get('ticker')}: " + " y ".join(partes) + "."


def _html_avisos(ctx: dict) -> str:
    partes = []
    nota = ctx.get("nota_seguimiento")
    if nota:
        partes.append(f'<div class="nota" role="alert">{esc(nota)}</div>')
    avisos = ctx.get("avisos_broker") or []
    if avisos:
        items = " ".join(esc(_frase_aviso(a)) for a in avisos)
        partes.append(
            '<div class="nota" role="alert"><b>El broker no cuadra con el seguimiento.</b> '
            f'{items} Sigue ocupando cupo y puede quedar desprotegida.</div>')
    return "".join(partes)


def _html_broker(ctx: dict) -> str:
    tz = ctx["tz"]

    def tabla(cabezas, filas, num=()):
        th = "".join(f"<th>{esc(h)}</th>" for h in cabezas)
        clase = f" class='{' '.join(f'n{i}' for i in num)}'" if num else ""
        return f"<div class='scroll'><table{clase}><thead><tr>{th}</tr></thead><tbody>{filas}</tbody></table></div>"

    def bloque(titulo, conocido, vacio, filas_html):
        if not conocido:
            cuerpo = '<p class="vacio falta">Sin datos.</p>'
        elif not filas_html:
            cuerpo = f'<p class="vacio">{vacio}</p>'
        else:
            cuerpo = filas_html
        return f"<h3>{titulo}</h3>{cuerpo}"

    pos = ctx.get("posiciones_broker")
    if pos:
        filas = "".join(
            "<tr>"
            f"<td class='tk'>{esc(p['ticker'])}</td><td>{esc(fmt_qty(p['qty']))}</td>"
            f"<td>{esc(fmt_dinero(p['entrada']))}</td><td>{esc(fmt_dinero(p['actual']))}</td>"
            f"<td>{_html_pnl(p['pnl'], p.get('pnl_pct'))}</td>"
            f"<td>{_html_nivel(p.get('stop'), p.get('mercado'), p.get('salidas_conocidas', True))}</td>"
            f"<td>{_html_nivel(p.get('tp'), conocido=p.get('salidas_conocidas', True))}</td>"
            f"<td>{_html_riesgo_fila(p, ctx.get('equity'))}</td><td>{_html_rr(p)}</td>"
            "</tr>" for p in pos)
        pos_html = tabla(("Ticker", "Cant.", "Entrada", "Actual", "P&L abierto", "Stop", "Objetivo",
                          "Riesgo al stop", "Obj./riesgo"), filas, num=(2, 3, 4, 5, 6, 7, 8, 9))
    else:
        pos_html = ""

    pend = ctx.get("pendientes_broker")
    if pend:
        filas = "".join(
            "<tr>"
            f"<td class='tk'>{esc(p['ticker'])}</td>"
            f"<td>{esc({'buy': 'compra', 'sell': 'venta'}.get(p.get('lado'), p.get('lado') or '—'))}</td>"
            f"<td>{esc(fmt_qty(p['qty']))}</td><td>{esc(fmt_dinero(p.get('limite')))}</td>"
            f"<td>{_html_nivel(p.get('stop'), p.get('mercado'), rol='pendiente')}</td>"
            f"<td>{_html_nivel(p.get('tp'), rol='pendiente')}</td>"
            f"<td>{_html_riesgo_fila(p, ctx.get('equity'))}</td><td>{_html_rr(p)}</td>"
            f"<td>{_html_espera(p.get('colocada'), ctx['ahora'], ctx.get('minutos_entrada_max')) if p.get('lado') == 'buy' else '—'}</td>"
            f"<td>{esc(ESTADOS_ORDEN.get(p.get('estado'), p.get('estado') or '—'))}</td>"
            "</tr>" for p in pend)
        pend_html = tabla(("Ticker", "Lado", "Cant.", "Límite", "Stop", "Objetivo",
                           "Riesgo si llena", "Obj./riesgo", "Esperando", "Estado"), filas, num=(3, 4, 5, 6, 7, 8, 9))
    else:
        pend_html = ""

    cerr = ctx.get("cerradas_hoy")
    if cerr:
        filas = "".join(
            "<tr>"
            f"<td class='tk'>{esc(c['ticker'])}</td><td>{esc(fmt_qty(c['qty']))}</td>"
            f"<td>{esc(fmt_dinero(c['entrada']))}</td><td>{esc(fmt_dinero(c['salida']))}</td>"
            f"<td>{_html_pnl(c['pnl'])}</td>"
            f"<td>{esc(_hora(c.get('hora'), tz, ahora=ctx['ahora']))}</td>"
            "</tr>" for c in cerr)
        cerr_html = tabla(("Ticker", "Cant.", "Entrada", "Salida", "P&L realizado", "Hora"), filas, num=(2, 3, 4, 5, 6))
    else:
        cerr_html = ""

    return (
        '<section class="panel" aria-label="Cuenta en el broker">'
        '<div class="titulo"><h2>Cuenta en el broker</h2>'
        '<span class="mono">Alpaca paper · posiciones, pendientes y cerradas hoy</span></div>'
        f'{_html_avisos(ctx)}'
        f'{bloque("Posiciones abiertas", pos is not None, "Sin posiciones abiertas.", pos_html)}'
        f'{bloque("Órdenes pendientes", pend is not None, "Sin órdenes pendientes.", pend_html)}'
        f'{bloque("Cerradas hoy", cerr is not None, "Ninguna posición cerrada hoy.", cerr_html)}'
        '</section>'
    )


CODIGOS_LEGIBLES = {
    "MERCADO_CERRADO": "Mercado cerrado", "PERDIDA_DIARIA": "Stop diario", "DATO_FALTANTE": "Falta dato",
    "CONCENTRACION": "Concentración máxima", "TICKER_COMPROMETIDO": "Ticker ya en juego",
    "RIESGO_POR_OPERACION": "Riesgo por operación", "PRECIO_FUERA_DE_ALCANCE": "Precio fuera de alcance",
    "FUERA_DE_BANDA": "Fuera de la banda de precio", "FRACCION_INSUFICIENTE": "Fracción insuficiente",
    "CIERRE_CERCANO": "Cierre cercano", "BLOQUEO_HALT": "Acción suspendida (halt)",
    "ACTIVO_NO_OPERABLE": "Activo no operable", "SIN_CODIGO": "Sin código",
}
DETALLES_LEGIBLES = {"ultimos_niveles_ts": "niveles recientes"}


def _codigo_legible(codigo) -> str:
    """Texto llano con el código técnico en el tooltip. MAXIMO_POSICIONES
    queda tal cual (el dueño lo lee así)."""
    cod = str(codigo or "")
    base, _, det = cod.partition(":")
    texto = CODIGOS_LEGIBLES.get(base)
    if texto is None:
        return esc(cod)
    if det:
        texto += ": " + DETALLES_LEGIBLES.get(det, det)
    return f'<span title="{esc(cod)}">{esc(texto)}</span>'


KNOBS_LEGIBLES = {
    "K1_saltar_segmento": "Saltar segmento perdedor", "K2_confianza_mas_uno": "Pedir +1 de confianza a la IA",
    "K3_max_posiciones": "Tope de posiciones", "K4_edad_max_senal": "Edad máxima de la señal",
    "K5_sin_small_caps": "Sin empresas chicas",
}
DIMENSIONES_LEGIBLES = {
    "patron": "patrón", "catalizador_tipo": "catalizador", "es_large_cap": "empresa grande",
    "banda_precio": "precio", "confianza": "confianza IA", "franja_et": "franja horaria",
    "espera_slot": "espera de cupo", "regimen": "régimen",
}


def _knob_legible(knob) -> str:
    k = str(knob)
    t = KNOBS_LEGIBLES.get(k)
    return f'<span title="{esc(k)}">{esc(t)} (en prueba)</span>' if t else esc(k)


def _html_intervalo(fila: dict, n: int, unidad: str) -> str:
    return f"desde {esc(fila['desde'])} hasta {esc(fila['hasta'])} ({n} {unidad})"


def _barra_uso(etiqueta: str, valor: float | None, tope: float | None, texto: str) -> str:
    """Una fila de uso de un tope: barra + texto. Sin valor o sin tope,
    solo el texto (que ya dice "sin dato")."""
    if valor is None or not tope:
        return f'<div class="uso"><span class="mono">{esc(etiqueta)}</span><b>{esc(texto)}</b></div>'
    frac = max(0.0, min(1.0, valor / tope))
    clase = "lleno" if valor >= tope else ("alto" if frac >= 0.8 else "")
    return (f'<div class="uso"><span class="mono">{esc(etiqueta)}</span><b>{esc(texto)}</b>'
            f'<div class="barra-uso"><div class="{clase}" style="width:{frac * 100:.0f}%"></div></div></div>')


def _html_uso_limites(ctx: dict) -> str:
    lim = ctx.get("limites") or {}
    filas = []
    comp, tope = lim.get("comprometidos"), lim.get("tope_cupo")
    if comp is None:
        filas.append(_barra_uso("Cupo de jugadas", None, None, "sin dato"))
    else:
        detalle = f" · {', '.join(comp)}" if comp else ""
        texto = f"{len(comp)} / {tope}{detalle}" if tope else f"{len(comp)} (tope sin dato){detalle}"
        filas.append(_barra_uso("Cupo de jugadas", float(len(comp)), tope, texto))
    tope_c = lim.get("tope_concentracion_pct")
    for c in lim.get("concentracion") or []:
        texto = ("sin dato" if c["pct"] is None
                 else f"{c['pct']:.1f}%" + (f" / {tope_c:.0f}%" if tope_c else ""))
        filas.append(_barra_uso(f"Concentración {c['ticker']}", c["pct"], tope_c, texto))
    rt = lim.get("riesgo_total")
    filas.append(_barra_uso(
        "Riesgo total al stop", None, None,
        "sin dato" if rt is None else fmt_dinero(rt) + (
            f" ({lim['riesgo_total_pct']:.2f}% del equity)" if lim.get("riesgo_total_pct") is not None else "")))
    exp = lim.get("expuesto")
    ef = lim.get("efectivo")
    filas.append(_barra_uso(
        "Invertido / efectivo", None, None,
        ("—" if exp is None else fmt_dinero(exp) + (f" ({lim['expuesto_pct']:.0f}%)" if lim.get("expuesto_pct") is not None else ""))
        + " · efectivo " + fmt_dinero(ef)))
    return '<div class="usos">' + "".join(filas) + "</div>"


def _html_riesgo(ctx: dict) -> str:
    sd = ctx.get("stop_diario") or {}
    if sd.get("activo") or sd.get("dato_faltante"):
        stop = _html_stop_diario(ctx)  # importa: se repite aquí
    else:
        stop = '<div class="nota-historial">Stop diario: ver <a href="#riesgo">Riesgo ↑</a></div>'
    return stop + _html_uso_limites(ctx) + _html_bloqueos(ctx)


def _html_stop_diario(ctx: dict) -> str:
    """Stop de pérdida diaria (2026-10-01): P&L de hoy vs umbral, estado
    (activo desde / inactivo) y último chequeo. Sin medición de hoy se
    dice así, sin fingir "inactivo". Dato faltante = rojo (`nota`)."""
    sd = ctx.get("stop_diario") or {"sin_datos": True}
    esc = html.escape
    if sd.get("sin_datos"):
        return ('<div class="nota-historial">Stop diario: sin medición hoy '
                '(se mide en sesión, en cada corrida del ejecutor)</div>')
    pct = sd.get("pct")
    titulo = f"Stop diario ({pct:.2f} %)" if pct is not None else "Stop diario"
    if sd.get("pnl") is not None and sd.get("umbral_usd") is not None:
        pnl_pct = f" ({sd['pnl_pct']:+.2f} %)" if sd.get("pnl_pct") is not None else ""
        cifras = f"P&L hoy {fmt_dinero(sd['pnl'], signo=True)}{_signo_menos(pnl_pct)} vs umbral −${sd['umbral_usd']:,.2f}"
    else:
        cifras = "P&L hoy sin dato"
    if sd.get("dato_faltante"):
        estado_txt = f"<b>sin datos ({esc(str(sd.get('codigo')))})</b> — no se abren entradas"
        clase = "nota"
    elif sd.get("activo"):
        estado_txt = (f"<b>ACTIVO desde {esc(sd.get('desde') or '?')}</b> — sin entradas nuevas hoy"
                      if sd.get("bloquea") else
                      f"<b>cruzado desde {esc(sd.get('desde') or '?')}</b> (modo {esc(str(sd.get('modo')))}: no bloquea)")
        clase = "nota-info"
    else:
        estado_txt = "inactivo"
        clase = "nota-info"
    modo = sd.get("modo")
    extra = f" · modo {esc(str(modo))}" if modo and modo != "enforce" else ""
    return (f'<div class="{clase}">{esc(titulo)}: {esc(cifras)} · {estado_txt}{extra}'
            f' · último chequeo {esc(sd.get("ultimo") or "—")}</div>')


# ───────────────────────── control de riesgo y aprendizaje (sombra) ─────────────────────────

def _leer_json(ruta: Path | None):
    """(dict|None, problema|None). Falta = (None, None); ilegible = problema."""
    if ruta is None or not ruta.exists():
        return None, None
    try:
        d = json.loads(ruta.read_text(encoding="utf-8"))
    except Exception as ex:
        return None, f"{ruta.name} ilegible ({type(ex).__name__})"
    return (d, None) if isinstance(d, dict) else (None, f"{ruta.name} con forma inesperada")


def leer_aprendizaje(carpeta: Path | None, eventos: list[dict], ahora: datetime) -> dict:
    """Todo lo que la sección "Control de riesgo y aprendizaje" muestra.
    Solo lectura de archivos del job nocturno y del ejecutor (sombra).
    Nada falta como 0: lo ausente queda None y se pinta "sin dato"."""
    out: dict = {"problemas": [], "reporte": None, "ajustes": None, "regimen": None, "sombra": []}
    if carpeta is None:
        out["info"] = "sin carpeta de aprendizaje configurada"
        return out
    carpeta = Path(carpeta)
    for clave, nombre in (("reporte", "ultimo_reporte.json"), ("ajustes", "ajustes.json"),
                          ("regimen", "regimen.json")):
        d, prob = _leer_json(carpeta / nombre)
        out[clave] = d
        if prob:
            out["problemas"].append(prob)
    ruta_sombra = carpeta / "sombra_diaria.jsonl"
    if ruta_sombra.exists():
        try:
            for linea in ruta_sombra.read_text(encoding="utf-8").splitlines():
                try:
                    d = json.loads(linea)
                except json.JSONDecodeError:
                    out["problemas"].append("sombra_diaria.jsonl con líneas ilegibles")
                    continue
                if isinstance(d, dict):
                    out["sombra"].append(d)
        except OSError as ex:
            out["problemas"].append(f"sombra_diaria.jsonl ilegible ({type(ex).__name__})")
    hoy = ahora.astimezone(NY).date().isoformat()
    gates = [e for e in eventos if e.get("tipo") == "gate_sombra" and e.get("_ts") is not None
             and e["_ts"].astimezone(NY).date().isoformat() == hoy]
    out["gates_hoy"] = len(gates)
    out["gates_bloquearian_hoy"] = sum(1 for e in gates if e.get("bloquearia"))
    out["gates_detalle"] = [{"ticker": e.get("ticker"), "knobs": sorted({b.get("knob") for b in e.get("bloquearia") or []
                                                                           if isinstance(b, dict)})}
                            for e in gates if e.get("bloquearia")][-10:]
    out["gates_error_hoy"] = sum(1 for e in eventos if e.get("tipo") == "gate_sombra_error" and e.get("_ts") is not None
                                 and e["_ts"].astimezone(NY).date().isoformat() == hoy)
    return out


def _signo_menos(t: str) -> str:
    """Un solo signo negativo en todo el panel: «−» (2026-10-01)."""
    return t.replace("-", "−")


def _fmt_r(v) -> str:
    return "sin dato" if v is None else _signo_menos(f"{v:+.2f}")


def _fmt_pct(v) -> str:
    return "sin dato" if v is None else f"{v * 100:.0f}%"


def _fmt_usd(v) -> str:
    """Dinero con el mismo formato que las cifras clave: «−$15.17»."""
    return "sin dato" if v is None else fmt_dinero(v, signo=True)


def _edad_txt(ts: str | None, ahora: datetime, tz) -> str:
    d = parse_ts(ts) if ts else None
    if d is None:
        return "sin dato"
    return f"{_hora(d, tz, ahora=ahora)} (hace {max(0, (ahora - d).total_seconds()) / 60:.0f} min)"


def _html_bloque_stop(ctx: dict) -> str:
    sd = ctx.get("stop_diario") or {"sin_datos": True}
    barra = ""
    if not sd.get("sin_datos") and sd.get("pnl") is not None and sd.get("umbral_usd"):
        perdida = max(0.0, -sd["pnl"])
        estado = ("ACTIVO" if sd.get("activo") else "inactivo")
        barra = _barra_uso("Pérdida del día vs umbral", perdida, sd["umbral_usd"],
                           f"{fmt_dinero(sd['pnl'], signo=True)} de −${sd['umbral_usd']:,.2f} · {estado}")
    elif sd.get("sin_datos"):
        barra = _barra_uso("Pérdida del día vs umbral", None, None, "sin dato")
    return f'<div class="usos">{barra}</div>{_html_stop_diario(ctx)}'


_SIMBOLO = {True: "✗ activa", False: "✓ no", None: "sin dato"}


def _tabla(cabeza: list[str], filas: str, num: tuple = ()) -> str:
    """Tabla con scroll interno (alto y ancho acotados): nunca ensancha la
    página. Las celdas `td.txt` (texto largo) envuelven."""
    ths = "".join(f"<th>{esc(c)}</th>" for c in cabeza)
    clase = f" class='{' '.join(f'n{i}' for i in num)}'" if num else ""
    return (f"<div class='tabla-ctl'><table{clase}><thead><tr>{ths}</tr></thead>"
            f"<tbody>{filas}</tbody></table></div>")


def _racha_vigente(ap: dict) -> tuple[dict | None, str]:
    """Racha en vivo (regimen.json) si la hay; si no, la del reporte."""
    reg = ap.get("regimen") or {}
    if isinstance(reg.get("racha"), dict) and reg["racha"]:
        return reg["racha"], "en vivo"
    rep = ap.get("reporte") or {}
    if isinstance(rep.get("racha"), dict) and rep["racha"]:
        return rep["racha"], f"reporte {rep.get('fecha') or 'sin dato'}"
    return None, "sin dato"


def _html_bloque_regimen(ap: dict, ctx: dict) -> str:
    reg = ap.get("regimen")
    tz, ahora = ctx["tz"], ctx["ahora"]
    if not reg:
        return ('<div class="nota-historial">Régimen SIN DATO: sin cálculo todavía (se calcula en sesión, al final de '
                'cada corrida del ejecutor). Fail-closed: el gate en sombra lo trata como CAUTELA.</div>')
    filas = "".join(
        f"<tr><td class='tk'>{esc(str(s.get('nombre')))}</td><td>{esc(_SIMBOLO.get(s.get('activa'), 'sin dato'))}</td>"
        f"<td class='txt'>{esc(str(s.get('detalle') or ''))}</td></tr>" for s in reg.get("senales") or [])
    rch = reg.get("racha") or {}
    filas += (f"<tr><td class='tk'>racha</td><td>{esc(_SIMBOLO.get(bool(rch.get('disparada')) if rch else None))}</td>"
              f"<td class='txt'>{esc(', '.join(rch.get('motivos') or []) or ('perdedores seguidos ' + str(rch.get('perdedores_seguidos')) if rch else 'sin dato'))}</td></tr>")
    acc = reg.get("acciones_sombra") or {}
    base = (ctx.get("limites") or {}).get("tope_cupo")
    acciones = []
    if acc.get("max_posiciones") is not None:
        acciones.append(f"máx posiciones {acc['max_posiciones']} (base {base if base is not None else 'sin dato'})")
    if acc.get("sin_small_caps"):
        acciones.append("small caps: bloqueadas")
    if acc.get("sin_apertura_ni_ultima_hora"):
        acciones.append("sin entradas en los primeros 30 min ni en la última hora")
    if acc.get("sin_entradas"):
        acciones.append("sin entradas nuevas")
    acciones_txt = "; ".join(acciones) or "ninguna (base)"
    return (_tabla(["Señal", "Estado", "Detalle"], filas)
            + f'<div class="nota-info">Nivel {esc(str(reg.get("nivel") or "SIN DATO"))} · Por qué: '
            f'{esc("; ".join(reg.get("motivos") or []) or "ninguna señal activa")} · '
            f'Acciones que tomaría (no aplicadas): {esc(acciones_txt)} · desde {esc(_edad_txt(reg.get("desde"), ahora, tz))}'
            f' · calculado {esc(_edad_txt(reg.get("calculado_en"), ahora, tz))}</div>')


def _html_bloque_aprendizaje(ap: dict, ctx: dict) -> str:
    rep = ap.get("reporte")
    if not rep:
        return '<div class="nota-historial">Sin reporte nocturno todavía (corre Lun–Vie 14:35 MTY / 16:35 ET).</div>'
    partes = []
    est = rep.get("estadisticas") or {}
    g = est.get("global") or {}
    n_min = est.get("n_minimo") or 20
    ses_min = est.get("sesiones_minimas") or 5
    insuf = (g.get("n") or 0) < n_min or (g.get("sesiones") or 0) < ses_min
    partes.append(
        f'<div class="stats s4"><div><span class="mono">Trades válidos</span><b>{esc(str(g.get("n", "sin dato")))}</b></div>'
        f'<div><span class="mono">Sesiones</span><b>{esc(str(g.get("sesiones", "sin dato")))}</b></div>'
        f'<div><span class="mono">R medio</span><b>{esc(_fmt_r(g.get("r_medio")))}</b></div>'
        f'<div><span class="mono">P&amp;L</span><b>{esc(_fmt_usd(g.get("pnl")))}</b></div></div>')
    if insuf:
        partes.append(f'<div class="nota-info">Muestra insuficiente: hacen falta n≥{n_min} y ≥{ses_min} sesiones '
                      f'por segmento para proponer. Reporte del {esc(str(rep.get("fecha")))}.</div>')
    segs = sorted((s for s in est.get("segmentos") or [] if s.get("n")),
                  key=lambda s: (-(s.get("n") or 0), str(s.get("dimension")), str(s.get("valor"))))
    if segs:
        filas = "".join(
            f"<tr><td class='tk' title='{esc(str(s.get('dimension')))}'>{esc(DIMENSIONES_LEGIBLES.get(str(s.get('dimension')), str(s.get('dimension'))))}"
            f" = {esc(str(s.get('valor')))}</td>"
            f"<td>{s.get('n')}/{n_min}<div class='barra-uso'><div style='width:{min(1.0, (s.get('n') or 0) / n_min) * 100:.0f}%'></div></div></td>"
            f"<td>{esc(str(s.get('sesiones')))}</td><td>{esc(_fmt_pct(s.get('wr')))}</td>"
            f"<td>{esc(_fmt_r(s.get('r_medio')))}</td><td>{esc(_fmt_r(s.get('r_shr')))}</td>"
            f"<td>{esc(_fmt_usd(s.get('pnl')))}</td><td>{esc(str(s.get('estado')))}</td></tr>" for s in segs)
        partes.append(_tabla(["Segmento", "n", "Ses.", "WR", "R", "R ajustado", "$", "Estado"], filas, num=(3, 4, 5, 6, 7)))
    return "".join(partes)


def _html_bloque_ajustes(ap: dict, ctx: dict) -> str:
    aj = ap.get("ajustes") or {}
    vig = aj.get("ajustes") or []
    if not vig:
        return ('<div class="nota-historial">Ajustes propuestos: ninguno. Aplicados: ninguno '
                '(encender knobs requiere GO del dueño).</div>')
    filas = "".join(
        f"<tr><td class='tk'>{_knob_legible(a.get('knob'))}</td><td>{esc(str(a.get('valor')))}</td>"
        f"<td class='txt'>{esc(str(a.get('motivo')))}</td><td>{esc(str(a.get('desde')))}</td><td>{esc(str(a.get('vence')))}</td>"
        f"<td>sombra</td></tr>" for a in vig)
    return (_tabla(["Knob", "Valor", "Por qué", "Desde", "Vence", "Estado"], filas)
            + '<div class="nota-historial">Aplicados: ninguno (encender knobs requiere GO del dueño).</div>')


def _html_bloque_sombra(ap: dict, ctx: dict) -> str:
    rep = ap.get("reporte")
    sombra = ap.get("sombra") or []
    retro = (rep or {}).get("sombra_retro_k3") or []
    filas = "".join(
        f"<tr><td class='tk'>{esc(str(d.get('fecha')))}</td><td>en vivo</td><td>{esc(_fmt_usd(d.get('pnl_real')))}</td>"
        f"<td>{esc(_fmt_usd(d.get('pnl_sombra')))}</td><td>{esc(_fmt_usd(d.get('delta')))}</td>"
        f"<td>{esc(str(d.get('bloqueados', 'sin dato')))} ({esc(str(d.get('ganadores_bloqueados', 'sin dato')))} gan.)</td></tr>"
        for d in reversed(sombra[-10:]))
    filas += "".join(
        f"<tr class='historial'><td class='tk'>{esc(str(d.get('fecha')))}</td><td>retro K3</td><td>{esc(_fmt_usd(d.get('pnl_real')))}</td>"
        f"<td>{esc(_fmt_usd(d.get('pnl_sombra')))}</td><td>{esc(_fmt_usd(d.get('delta')))}</td>"
        f"<td>{esc(str(d.get('bloqueados')))} ({esc(str(d.get('ganadores_bloqueados')))} gan.)</td></tr>" for d in reversed(retro[-10:]))
    acum_vivo = round(sum(d.get("delta") or 0 for d in sombra if d.get("delta") is not None), 2) if sombra else None
    partes = [f'<div class="nota-info">Gates hoy: {ap.get("gates_hoy", 0)} evaluados, '
              f'{ap.get("gates_bloquearian_hoy", 0)} habrían bloqueado'
              + (f', {ap["gates_error_hoy"]} con error' if ap.get("gates_error_hoy") else "")
              + f' · Δ acumulado en vivo: {esc(_fmt_usd(acum_vivo))} USD. Retro = in-sample, solo orientativo.</div>']
    if filas:
        partes.append(_tabla(["Día", "Tipo", "Real $", "Sombra $", "Δ", "Bloqueados"], filas, num=(3, 4, 5)))
    else:
        partes.append('<div class="vacio">Sin días de sombra todavía.</div>')
    return "".join(partes)


def _tarjeta_ctl(etiqueta: str, valor: str, sub: str, clase: str = "", extra: str = "") -> str:
    return (f'<div class="ctl-card {clase}"><span class="mono">{esc(etiqueta)}</span>'
            f'<b>{valor}</b><span class="ctl-sub">{sub}</span>{extra}</div>')


def _html_resumen_control(ap: dict, ctx: dict) -> str:
    """Fila de 4 tarjetas: stop diario, régimen, racha, ajustes."""
    tarjetas = []
    # 1. stop diario (estado de #236)
    sd = ctx.get("stop_diario") or {"sin_datos": True}
    if sd.get("sin_datos"):
        tarjetas.append(_tarjeta_ctl("Stop diario", "sin dato", "sin medición hoy"))
    else:
        if sd.get("pnl") is not None and sd.get("umbral_usd"):
            valor = f"{fmt_dinero(sd['pnl'], signo=True)} de −${sd['umbral_usd']:,.2f}"
            frac = max(0.0, min(1.0, max(0.0, -sd["pnl"]) / sd["umbral_usd"]))
            cl = "lleno" if frac >= 1 else ("alto" if frac >= 0.8 else "")
            barra = f'<div class="barra-uso"><div class="{cl}" style="width:{frac * 100:.0f}%"></div></div>'
        else:
            valor, barra = "sin dato", ""
        if sd.get("dato_faltante"):
            estado, clase = "sin datos: no se abren entradas", "mal"
        elif sd.get("activo"):
            estado = ("ACTIVO: sin entradas nuevas" if sd.get("bloquea") else "cruzado (no bloquea)")
            clase = "mal"
        else:
            estado, clase = "inactivo", ""
        if sd.get("ultimo"):
            estado += f" · medido {sd['ultimo']}"
        tarjetas.append(_tarjeta_ctl("Stop diario", esc(valor), esc(estado), clase, barra))
    # 2. régimen
    reg = ap.get("regimen")
    if reg:
        senales = reg.get("senales") or []
        activas = sum(1 for s in senales if s.get("activa") is True)
        nivel = str(reg.get("nivel") or "SIN DATO")
        tarjetas.append(_tarjeta_ctl("Régimen · sombra", esc(nivel),
                                     f"{activas} de {len(senales)} señales activas",
                                     "" if nivel == "NORMAL" else "aviso"))
    else:
        tarjetas.append(_tarjeta_ctl("Régimen · sombra", "SIN DATO", "se calcula en sesión"))
    # 3. racha
    rch, origen = _racha_vigente(ap)
    if rch:
        disp = bool(rch.get("disparada"))
        r10 = rch.get("r_ultimos_10")
        sub = (f"perdedores seguidos {esc(str(rch.get('perdedores_seguidos', 'sin dato')))} · "
               f"últ. 10: {esc(_fmt_r(r10))}R · {esc(origen)}")
        tarjetas.append(_tarjeta_ctl("Racha", "disparada" if disp else "no disparada", sub, "aviso" if disp else ""))
    else:
        tarjetas.append(_tarjeta_ctl("Racha", "sin dato", "sin memoria de trades"))
    # 4. ajustes propuestos
    aj = ap.get("ajustes")
    gates = f"chequeos en prueba hoy: {ap.get('gates_bloquearian_hoy', 0)} de {ap.get('gates_hoy', 0)} habrían frenado"
    if aj is None:
        tarjetas.append(_tarjeta_ctl("Ajustes propuestos", "sin dato", esc(gates)))
    else:
        vig = aj.get("ajustes") or []
        knobs = ", ".join(_knob_legible(k) for k in sorted({str(a.get("knob")) for a in vig})) or "ninguno"
        tarjetas.append(_tarjeta_ctl("Ajustes propuestos", str(len(vig)),
                                     f"{knobs} · {esc(gates)}", "aviso" if vig else ""))
    return '<div class="ctl-resumen">' + "".join(tarjetas) + "</div>"


def _detalle(id_: str, titulo: str, resumen: str, cuerpo: str, abierto: bool = False) -> str:
    return (f'<details class="ctl" id="{esc(id_)}"{" open" if abierto else ""}>'
            f'<summary><b>{esc(titulo)}</b><span class="mono">{esc(resumen)}</span></summary>'
            f'<div class="ctl-cuerpo">{cuerpo}</div></details>')


def _html_control_aprendizaje(ctx: dict) -> str:
    """Sección "Control de riesgo y aprendizaje" (2026-10-01, compacta):
    fila de tarjetas + detalles plegables con tablas de scroll interno.
    Solo lectura; una falla en un bloque no tumba el panel."""
    ap = ctx.get("aprendizaje") or {"problemas": ["sin datos de aprendizaje"]}
    partes = []
    try:
        partes.append(_html_resumen_control(ap, ctx))
    except Exception as ex:
        partes.append(f'<div class="nota">resumen no disponible ({esc(type(ex).__name__)})</div>')
    rep = ap.get("reporte") or {}
    g = ((rep.get("estadisticas") or {}).get("global") or {})
    detalles = (
        ("ctl-stop", "Stop diario", "P&L del día vs umbral (1 %)", lambda: _html_bloque_stop(ctx)),
        ("ctl-regimen", "Modo defensivo / régimen", "señales y acciones que tomaría (sombra)",
         lambda: _html_bloque_regimen(ap, ctx)),
        ("ctl-segmentos", "Aprendizaje: segmentos",
         f"n={g.get('n', 'sin dato')} · reporte {rep.get('fecha') or 'sin dato'} · sombra: nada se aplica",
         lambda: _html_bloque_aprendizaje(ap, ctx)),
        ("ctl-ajustes", "Ajustes propuestos (en sombra)", "el motor nunca afloja", lambda: _html_bloque_ajustes(ap, ctx)),
        ("ctl-sombra", "Qué habría hecho la sombra", "real vs sombra por día", lambda: _html_bloque_sombra(ap, ctx)),
    )
    for id_, titulo, resumen, fn in detalles:
        try:
            cuerpo = fn()
        except Exception as ex:
            cuerpo = f'<div class="nota">bloque no disponible ({esc(type(ex).__name__)})</div>'
        partes.append(_detalle(id_, titulo, resumen, cuerpo))
    if ap.get("info"):
        partes.append(f'<div class="nota-historial">{esc(ap["info"])}</div>')
    for p in ap.get("problemas") or []:
        partes.append(f'<div class="nota">{esc(p)}</div>')
    return "".join(partes)


# ───────────────────────── trades de días anteriores (solo lectura) ─────────────────────────

MTY = ZoneInfo("America/Monterrey")
HISTORIAL_MAX_SESIONES = 20
_MOTIVO_SALIDA = {"stop": "stop", "objetivo": "objetivo", "cierre": "cierre EOD",
                  "arrastre_overnight": "arrastre overnight"}


def _num_o_none(v) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN = sin dato


def leer_historial_trades(carpeta: Path | None, max_sesiones: int = HISTORIAL_MAX_SESIONES) -> dict:
    """Trades cerrados de la memoria (`memoria_trades.jsonl`, la escribe el
    job nocturno con fills paper de Alpaca). Agrupados por día ET, más
    reciente primero. Lo ausente queda None ("sin dato"), nunca 0."""
    out: dict = {"dias": [], "problemas": [], "ocultas": 0, "info": None, "actualizado": None}
    if carpeta is None:
        out["info"] = "sin carpeta de aprendizaje configurada"
        return out
    ruta = Path(carpeta) / "memoria_trades.jsonl"
    if not ruta.exists():
        out["info"] = "Sin memoria de trades todavía (la escribe el job nocturno, Lun–Vie 14:35 MTY / 16:35 ET)."
        return out
    try:
        lineas = ruta.read_text(encoding="utf-8").splitlines()
        out["actualizado"] = datetime.fromtimestamp(ruta.stat().st_mtime, timezone.utc)
    except OSError as ex:
        out["problemas"].append(f"memoria_trades.jsonl ilegible ({type(ex).__name__})")
        return out
    malas = 0
    vistos: set = set()
    por_dia: dict[str, list[dict]] = {}
    for linea in lineas:
        if not linea.strip():
            continue
        try:
            d = json.loads(linea)
        except json.JSONDecodeError:
            malas += 1
            continue
        if not isinstance(d, dict):
            malas += 1
            continue
        clave = d.get("order_id") or (d.get("ticker"), d.get("entrada_ts"))
        if clave in vistos:
            continue
        vistos.add(clave)
        t_in, t_out = parse_ts(d.get("entrada_ts")), parse_ts(d.get("salida_ts"))
        dia = t_in.astimezone(NY).date().isoformat() if t_in else (d.get("fecha") or None)
        if not dia:
            malas += 1
            continue
        por_dia.setdefault(str(dia), []).append({
            "ticker": d.get("ticker"), "entrada": t_in, "salida": t_out,
            "p_in": _num_o_none(d.get("precio_fill")), "p_out": _num_o_none(d.get("precio_salida")),
            "cantidad": _num_o_none(d.get("cantidad")), "pnl": _num_o_none(d.get("pnl")),
            "r": _num_o_none(d.get("r")), "motivo": d.get("motivo_salida")})
    if malas:
        out["problemas"].append(f"memoria_trades.jsonl: {malas} línea(s) ilegibles o sin fecha")
    dias = sorted(por_dia, reverse=True)
    out["ocultas"] = max(0, len(dias) - max_sesiones)
    for dia in dias[:max_sesiones]:
        trades = sorted(por_dia[dia], key=lambda t: (t["entrada"] is None, t["entrada"] or datetime.min.replace(tzinfo=timezone.utc)))
        pnls = [t["pnl"] for t in trades if t["pnl"] is not None]
        out["dias"].append({
            "fecha": dia, "trades": trades, "n": len(trades),
            "ganadores": sum(1 for p in pnls if p > 0), "perdedores": sum(1 for p in pnls if p < 0),
            "sin_pnl": len(trades) - len(pnls),
            "pnl": round(sum(pnls), 2) if pnls else None})
    return out


_DIAS_ES = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")


def _fecha_es(iso: str) -> str:
    try:
        d = datetime.strptime(iso, "%Y-%m-%d")
    except (TypeError, ValueError):
        return str(iso)
    return f"{_DIAS_ES[d.weekday()]} {d.strftime('%Y-%m-%d')}"


def _hora_mty(d: datetime | None, ref_dia: str) -> str:
    if d is None:
        return "sin dato"
    local = d.astimezone(MTY)
    if local.date().isoformat() != ref_dia:
        return local.strftime("%m-%d %H:%M")
    return local.strftime("%H:%M")


def _fmt_precio(v: float | None) -> str:
    return "sin dato" if v is None else f"{v:,.2f}"


def _fmt_cant(v: float | None) -> str:
    if v is None:
        return "sin dato"
    return f"{int(v)}" if float(v).is_integer() else f"{v:g}"


def _html_historial_trades(ctx: dict) -> str:
    """Sección "Trades de días anteriores". Una falla no tumba el panel."""
    try:
        return _html_historial_cuerpo(ctx)
    except Exception as ex:
        return f'<div class="nota">historial no disponible ({esc(type(ex).__name__)})</div>'


def _html_historial_cuerpo(ctx: dict) -> str:
    h = ctx.get("historial_trades") or {"dias": [], "problemas": ["sin datos de historial"]}
    partes = []
    hoy = ctx["ahora"].astimezone(NY).date().isoformat()
    for i, dia in enumerate(h.get("dias") or []):
        filas = "".join(
            f"<tr><td class='tk'>{esc(str(t['ticker'] or 'sin dato'))}</td>"
            f"<td>{esc(_hora_mty(t['entrada'], dia['fecha']))}</td><td>{esc(_hora_mty(t['salida'], dia['fecha']))}</td>"
            f"<td>{esc(_fmt_precio(t['p_in']))}</td><td>{esc(_fmt_precio(t['p_out']))}</td>"
            f"<td>{esc(_fmt_cant(t['cantidad']))}</td>"
            f"<td class='{'' if t['pnl'] is None else ('pos' if t['pnl'] > 0 else ('neg' if t['pnl'] < 0 else ''))}'>{esc(_fmt_usd(t['pnl']))}</td>"
            f"<td>{esc(_fmt_r(t['r']))}</td>"
            f"<td>{esc(_MOTIVO_SALIDA.get(t['motivo'], str(t['motivo'])) if t['motivo'] else 'sin dato')}</td></tr>"
            for t in dia["trades"])
        pnl = dia["pnl"]
        pnl_txt = _fmt_usd(pnl)
        if pnl is not None and dia["sin_pnl"]:
            pnl_txt += f" (parcial: {dia['sin_pnl']} sin dato)"
        clase_pnl = "" if pnl is None else ("pos" if pnl > 0 else ("neg" if pnl < 0 else ""))
        etiqueta = _fecha_es(dia["fecha"]) + (" · hoy" if dia["fecha"] == hoy else "")
        resumen = (f'<span class="mono">{dia["n"]} trade{"s" if dia["n"] != 1 else ""} · '
                   f'{dia["ganadores"]} G / {dia["perdedores"]} P</span>'
                   f'<span class="mono {clase_pnl}">P&amp;L {esc(pnl_txt)}</span>')
        cuerpo = _tabla(["Ticker", "Entrada", "Salida", "P. entrada", "P. salida", "Acciones", "P&L $", "R", "Motivo"],
                        filas, num=(4, 5, 6, 7, 8))
        partes.append(f'<details class="ctl dia" id="hist-{esc(dia["fecha"])}"{" open" if i == 0 else ""}>'
                      f'<summary><b>{esc(etiqueta)}</b>{resumen}</summary>'
                      f'<div class="ctl-cuerpo">{cuerpo}</div></details>')
    if not h.get("dias") and not h.get("problemas"):
        partes.append(f'<div class="nota-historial">{esc(h.get("info") or "Sin trades cerrados todavía.")}</div>')
    if h.get("ocultas"):
        partes.append(f'<div class="nota-historial">{h["ocultas"]} sesión(es) más antiguas no se muestran '
                      f'(límite {HISTORIAL_MAX_SESIONES}).</div>')
    for p in h.get("problemas") or []:
        partes.append(f'<div class="nota">{esc(p)}</div>')
    return "".join(partes)


def _html_bloqueos(ctx: dict) -> str:
    """Panel de límites. Lo activo va en su sección; el día, en gris,
    con desde/hasta y cuándo se vio por última vez. El rojo (`nota`)
    solo si la ventana activa pide revisar. MERCADO_CERRADO no se
    presenta como capacidad llena."""
    r = ctx.get("riesgo") or {}
    unicos = r.get("unicos") or []
    capacidad = r.get("capacidad") or []
    informativos = r.get("informativos") or []
    hay_filas = bool(unicos or capacidad or informativos)
    sin_recientes = bool(ctx.get("sin_datos_recientes"))

    if not hay_filas:
        if ctx.get("conteos_validos") and not sin_recientes:
            return '<p class="vacio">Ningún límite ha bloqueado operaciones hoy.</p>'
        # Sin log, o sesión abierta sin un ciclo reciente: explícito, y
        # no un cero que parezca "no pasó nada". Fuera de sesión, sin
        # ciclo, se queda el "Sin datos" de siempre.
        if sin_recientes:
            return f'<p class="vacio falta">Sin datos recientes: {esc(ctx.get("motivo_sin_datos"))}.</p>'
        return f'<p class="vacio falta">Sin datos: {esc(ctx.get("motivo_sin_datos"))}.</p>'

    partes: list[str] = []
    if sin_recientes and not r.get("revisar"):
        partes.append(
            f'<p class="vacio falta">Sin datos recientes: {esc(ctx.get("motivo_sin_datos"))}.</p>'
        )

    for c in informativos:
        frase = _html_intervalo(c, c["corridas"], "corridas")
        if c.get("activo"):
            partes.append(
                f'<div class="nota-info">Mercado cerrado · <b>{_codigo_legible(c["codigo"])}</b> · {frase}</div>'
            )
        else:
            partes.append(
                f'<div class="nota-historial">Historial: <b>{_codigo_legible(c["codigo"])}</b> · {frase}'
                f' · última {esc(c["hasta"])} · resuelto</div>'
            )

    for c in capacidad:
        frase = _html_intervalo(c, c["corridas"], "corridas")
        if c.get("activo"):
            # Tope lleno AHORA: el control funciona, no es alarma. El stop
            # diario (2026-10-01) se nombra como tal: corta la sesión entera.
            etiqueta = "Stop diario activo" if c.get("codigo") == "PERDIDA_DIARIA" else "Capacidad llena"
            partes.append(
                f'<div class="nota-info">{etiqueta}: <b>{_codigo_legible(c["codigo"])}</b> · {frase}'
                f' · {esc(c["motivo"])}</div>'
            )
        else:
            partes.append(
                f'<div class="nota-historial">Historial: <b>{_codigo_legible(c["codigo"])}</b> · {frase}'
                f' · última {esc(c["hasta"])} · resuelto</div>'
            )

    activos = [f for f in unicos if f.get("activo")]
    historial = [f for f in unicos if not f.get("activo")]

    def _tabla(filas: list[dict], es_historial: bool) -> str:
        cuerpo = []
        for f in filas[:20]:
            cls = ' class="historial"' if es_historial else ""
            ultima = esc(f["hora"]) + (" · resuelto" if es_historial else "")
            cuerpo.append(
                f"<tr{cls}><td class='tk'>{esc(f['ticker'])}</td><td>{_codigo_legible(f['codigo'])}</td>"
                f"<td>{f['veces']}</td><td>{esc(f['desde'])}</td><td>{esc(f['hasta'])}</td>"
                f"<td>{ultima}</td></tr>"
            )
        return (
            "<div class='scroll'><table><thead><tr><th>Ticker</th><th>Código</th><th>Veces</th>"
            "<th>Desde</th><th>Hasta</th><th>Última</th></tr></thead><tbody>"
            + "".join(cuerpo) + "</tbody></table></div>"
        )

    if activos:
        partes.append('<h3>Activo ahora</h3>')
        partes.append(_tabla(activos, False))
    if historial:
        # Plegado y con scroll interno (2026-10-01): ya no ensancha el panel.
        partes.append(f'<details class="ctl" id="lim-historial"><summary><b class="historial">Historial del día</b>'
                      f'<span class="mono">{len(historial)} ya resueltos</span></summary><div class="ctl-cuerpo">'
                      + _tabla(historial, True).replace("<div class='scroll'>", "<div class='tabla-ctl'>", 1)
                      + '</div></details>')

    # El resumen va en rojo SOLO si la ventana activa pide revisar.
    hay_revisar = bool(r.get("dato_faltante") or r.get("codigos_nuevos"))
    partes.append(
        f'<div class="{"nota" if hay_revisar else "nota-info"}">'
        f'{len(unicos)} bloqueos únicos · {r.get("eventos", 0)} eventos'
        + (f' · <b>revisar:</b> {esc(", ".join(r["dato_faltante"]))}' if r.get("dato_faltante") else "")
        + (f' · <b>motivo nuevo:</b> {esc(", ".join(r["codigos_nuevos"]))}' if r.get("codigos_nuevos") else "")
        + '</div>'
    )
    return "".join(partes)


ESTADOS_WATCH = {
    "triggered": "disparada", "watching": "vigilando", "expired": "expirada",
    "invalidated": "invalidada", "missed": "perdida", "archived": "archivada",
}


def _html_watchlist(ctx: dict) -> tuple[str, str]:
    """Activas arriba (disparadas primero), terminales de hoy plegadas.

    El 29/9 la tabla tenía ~40 filas y las expiradas de ayer se mezclaban
    con las vivas; ETN y CDNS salían dos veces sin que se viera que una
    era la entrada vieja. Las terminales siguen ahí, solo plegadas."""
    tz, ahora = ctx["tz"], ctx["ahora"]
    todas = ctx.get("watch") or []
    if not todas:
        return '<p class="vacio">Sin tickers en observación ni cambios de estado hoy.</p>', ""

    def estado(w):
        return str(w.get("estado") or "").lower()

    minimo = datetime.min.replace(tzinfo=timezone.utc)
    activas = sorted((w for w in todas if estado(w) in ESTADOS_ACTIVOS),
                     key=lambda w: (estado(w) != "triggered", -(w.get("detectado") or minimo).timestamp()))
    terminales = [w for w in todas if estado(w) not in ESTADOS_ACTIVOS]

    def filas(items):
        return "".join(
            f"<tr><td class='tk'><span class='dot est-{esc(estado(w))}' title='{esc(ESTADOS_WATCH.get(estado(w), w['estado']))}'></span>"
            f"{esc(w['ticker'])}</td><td>{esc(w['cap'])}</td>"
            f"<td class='cat' tabindex='0' title='{esc(w['catalizador'])}'><span>{esc(w['catalizador'])}</span></td>"
            f"<td>{_hora(w['detectado'], tz, ahora=ahora)}</td>"
            f"<td class='col-estado'><span class='est est-{esc(estado(w))}' title='{esc(w['estado'])}'>"
            f"{esc(ESTADOS_WATCH.get(estado(w), w['estado']))}</span></td></tr>" for w in items)

    cab = ("<thead><tr><th>Ticker</th><th>Cap</th><th>Catalizador</th><th>Detectado</th>"
           "<th class='col-estado'>Estado</th></tr></thead>")

    def tabla(items):
        return f"<div class='tabla-ctl tabla-watch'><table>{cab}<tbody>{filas(items)}</tbody></table></div>"

    disparadas = [w for w in activas if estado(w) == "triggered"]
    vigilando = [w for w in activas if estado(w) != "triggered"]
    partes = []
    if disparadas:
        partes.append(tabla(disparadas))
    elif activas:
        partes.append('<p class="vacio">Ninguna disparada ahora.</p>')
    else:
        partes.append('<p class="vacio">Sin tickers activos ahora.</p>')
    if vigilando:
        partes.append(f"<details class='ctl' id='wl-vigilando'><summary><b>Vigilando</b>"
                      f"<span class='mono'>{len(vigilando)} ticker{'s' if len(vigilando) != 1 else ''} esperando la ruptura</span>"
                      f"</summary><div class='ctl-cuerpo'>{tabla(vigilando)}</div></details>")
    if terminales:
        partes.append(f"<details class='terminales'><summary class='mono'>Cerradas hoy en la watchlist "
                      f"({len(terminales)}): expiradas, invalidadas, archivadas</summary>"
                      f"{tabla(terminales)}</details>")
    n_disp = sum(1 for w in activas if estado(w) == "triggered")
    n_vig = len(activas) - n_disp
    bandas: dict[str, int] = {}
    for w in activas:
        clave = str(w.get("cap") or "—")
        bandas[clave] = bandas.get(clave, 0) + 1
    txt_bandas = " · ".join(f"{n} {b}" for b, n in sorted(bandas.items()))
    resumen = f"{n_disp} disparadas · {n_vig} vigilando" + (f" · {txt_bandas}" if txt_bandas else "") + " · "
    return "".join(partes), resumen


def _html_banda_datos(ctx: dict) -> str:
    """Avisos de falla de datos de mercado arriba, a la vista (2026-10-01).
    Antes vivían dentro de Velas. Gris = informativo, rojo = falla."""
    avisos = ctx.get("avisos_feed") or []
    if not avisos:
        return ""
    fuente = ctx.get("fuente_datos")
    pre = f"Datos de mercado: {esc(fuente)}. " if fuente else "Datos de mercado: "
    # Todos son fallas: con el plan de pago (1/10) un 401/403 ya no es
    # informativo.
    clase = "nota"
    return (f'<div class="{clase} banda-datos" role="status">{pre}'
            + " · ".join(esc(a) for a in avisos) + "</div>")


ANCLAS = (("resumen", "Resumen"), ("riesgo", "Riesgo"), ("posiciones", "Posiciones"), ("equity", "Equity"),
          ("velas", "Velas"), ("watchlist", "Watchlist"), ("ejecucion", "Ejecución"), ("historial", "Historial"),
          ("sistema", "Sistema"))


def _html_anclas() -> str:
    """Índice fijo bajo el encabezado (2026-10-01). En móvil, desplegable."""
    links = "".join(f'<a href="#{i}">{esc(t)}</a>' for i, t in ANCLAS)
    return (f'<nav class="anclas" aria-label="Índice del panel">{links}</nav>'
            f'<details class="anclas-movil"><summary>Ir a sección ▾</summary><nav aria-label="Índice del panel (móvil)">{links}</nav></details>')


def _html_sistema(ctx: dict, etapas_html: str) -> str:
    """Etapas del sistema como franja de una línea; se abre sola si alguna
    pide revisión o no tiene datos (presentación; el cálculo no cambia)."""
    etapas = ctx.get("etapas") or []
    if not etapas:
        return ('<details class="sistema" id="sistema" data-forzar="1" open><summary><b>Sistema</b>'
                '<span class="punto sin-datos">sin dato</span></summary></details>')
    raras = [e for e in etapas if e.get("estado") in ("alerta", "sin-datos")]
    ok = sum(1 for e in etapas if e.get("estado") == "ok")
    if raras:
        txt = "Revisar: " + ", ".join(str(e.get("nombre")) for e in raras)
        clase = "alerta"
    else:
        txt, clase = "OK", "ok"
    detalle = f"{ok}/{len(etapas)} etapas OK"
    forzar = ' data-forzar="1" open' if raras else ""
    return (f'<details class="sistema" id="sistema"{forzar}><summary><b>Sistema</b>'
            f'<span class="punto {clase}">{esc(txt)}</span><span class="mono">{esc(detalle)} · tocar para ver el detalle</span>'
            f'</summary><section class="fila c4" aria-label="Etapas del sistema">{etapas_html}</section></details>')


_SIN_FILL = {"cancelada", "rechazada", "expirada", "abierta", "en espera"}


def _precio_stream(s: dict) -> str:
    """Precio del fill. Una orden que no llenó no tiene precio: «—» (no
    aplica), distinto de «sin dato» (debía tenerlo y no llegó)."""
    if s.get("precio") is not None:
        return fmt_dinero(s["precio"])
    return "—" if s.get("estado") in _SIN_FILL else "sin dato"


_ZONAS_CORTAS = {"America/Monterrey": "MTY", "America/Mexico_City": "CDMX", "America/New_York": "ET"}


def etiqueta_zona(tz, ahora: datetime) -> str:
    """«MTY (UTC−6)»: zona corta + desfase real de ese momento."""
    nombre = str(tz)
    if nombre == "UTC":
        return "UTC"
    off = ahora.astimezone(tz).utcoffset()
    if off is None:
        return nombre
    minutos = int(off.total_seconds() // 60)
    signo = "+" if minutos >= 0 else "−"
    h, m = divmod(abs(minutos), 60)
    desfase = f"UTC{signo}{h}" + (f":{m:02d}" if m else "")
    return f"{_ZONAS_CORTAS.get(nombre, nombre)} ({desfase})"


def render(ctx: dict) -> str:
    tz = ctx["tz"]
    etiqueta_tz = etiqueta_zona(tz, ctx["ahora"])

    problemas = ""
    if ctx["problemas"]:
        items = "".join(f"<li>{esc(p)}</li>" for p in ctx["problemas"])
        problemas = f'<section class="problemas" role="alert"><b>Datos incompletos.</b> Lo que no se pudo leer aparece como «sin dato».<ul>{items}</ul></section>'

    etapas = "".join(f"""
<div class="panel etapa">
  <div class="titulo"><span class="mono">{esc(e['donde'])}</span><span class="punto {e['estado']}">{ {'ok':'OK','alerta':'Revisar','sin-datos':'Sin datos','info':'Info'}[e['estado']] }</span></div>
  <div class="nombre">{esc(e['nombre'])}</div>
  <div class="rol">{esc(e['rol'])}</div>
  <div class="pie mono"><span>{esc(e['detalle'])}</span></div>{f'<div class="mono historico">{esc(e["nota"])}</div>' if e.get("nota") else ""}
</div>""" for e in ctx["etapas"])

    signo = "" if ctx["pnl"] is None else ("pos" if ctx["pnl"] >= 0 else "neg")
    pct = "" if ctx["pnl_pct"] is None else f" ({ctx['pnl_pct']:+.2f}%)"
    ordenes_sub = "—" if ctx["n_rech"] is None else f"{ctx['n_rech']} rechazadas"
    lat_sub = ("sin compras hoy" if ctx["lat_mediana"] is None
               else f"lo típico hoy en comprar · límite {fmt_num(ctx['presupuesto'])} min")
    kpis = [
        ("Equity paper", fmt_dinero(ctx["equity"]),
         "", f"cuenta paper {ctx['cuenta_numero']}" if ctx["cuenta_numero"] else "cuenta de práctica Alpaca"),
        ("P&L del día", fmt_dinero(ctx["pnl"], signo=True), signo,
         f"vs cierre anterior{_signo_menos(pct)} · a las {_hora(ctx['ahora'], tz)}"),
        ("Posiciones", fmt_num(ctx["n_pos"]), "", "abiertas ahora"),
        ("Órdenes hoy", fmt_num(ctx["n_ord"]), "", ordenes_sub),
        ("Latencia (reacción)", fmt_num(ctx["lat_mediana"], " min"), "", lat_sub),
    ]
    kpis_html = "".join(
        f'<div class="panel kpi"><span class="mono">{esc(a)}</span><span class="valor {c}">{esc(b)}</span><span class="mono">{esc(d)}</span></div>'
        for a, b, c, d in kpis)

    watch, resumen_watch = _html_watchlist(ctx)

    if ctx["stream"]:
        filas = "".join(
            f"<tr><td>{esc(s['hora'])}</td><td class='tk'>{esc(s['ticker'])}</td><td>{esc(s['lado'])}</td>"
            f"<td>{esc(s['estado'])}</td><td>{esc(_precio_stream(s))}</td></tr>" for s in ctx["stream"])
        stream = f"<div class='scroll'><table class='n5'><tbody>{filas}</tbody></table></div>"
    else:
        stream = '<p class="vacio">Sin órdenes hoy.</p>'

    if ctx["dudas"]:
        dudas = "".join(
            f'<div class="duda"><div class="cab"><b>{esc(d["ticker"])}</b><span>{esc(d["hora"])}</span></div><p>{esc(d["motivo"])}</p></div>'
            for d in ctx["dudas"])
    else:
        dudas = ('<p class="vacio">El ejecutor no ha rechazado entradas hoy.</p>' if ctx["conteos_validos"]
                 else f'<p class="vacio falta">Sin datos: {esc(ctx["motivo_sin_datos"])}.</p>')

    riesgo = _html_riesgo(ctx)

    # El título dice de qué sesión son los puntos. Antes de la apertura
    # Alpaca manda la sesión anterior completa: eso no es "hoy" y se avisa.
    dia = ctx["equity_dia"]
    if dia["puntos"] and not dia["es_hoy"]:
        titulo_dia = "Equity de la última sesión"
        sub_dia = f"{_fecha_corta(dia['sesion'])} · velas de 5 min"
        nota_dia = (f'<div class="nota-info">Sin sesión hoy todavía: Alpaca devuelve la última sesión '
                    f'que tiene, la del {esc(_fecha_corta(dia["sesion"]))}.</div>')
    else:
        titulo_dia = "Equity de hoy"
        sub_dia = f"{_fecha_corta(dia['sesion'])} · velas de 5 min" if dia["puntos"] else "velas de 5 min"
        nota_dia = ""
    equity_html = (
        f'<div class="panel"><div class="titulo"><h2>{titulo_dia}</h2><span class="mono">{esc(sub_dia)}</span></div>'
        f'{_grafico_equity(dia, tz, "dia")}{_resumen_equity(dia, tz)}{nota_dia}</div>'
        f'<div class="panel"><div class="titulo"><h2>Equity del último mes</h2><span class="mono">cierre diario</span></div>'
        f'{_grafico_equity(ctx["equity_mes"], tz, "mes")}{_resumen_equity(ctx["equity_mes"])}'
        + (f'<div class="nota">{esc(ctx["desajuste_equity"])}</div>' if ctx["desajuste_equity"] else "")
        + '</div>')

    if ctx["operaciones"]:
        def _tarjeta(op):
            marca_pendiente = '<span class="badge">pendiente</span>' if op.get("rol") == "pendiente" else ""
            nota_velas = (f'<div class="nota">{esc(op["velas"]["error"])}</div>'
                          if op["velas"].get("error") and op["velas"].get("velas") else "")
            nota_pendiente = ('<p class="vacio">Orden de entrada sin llenar. La entrada se marca cuando hay fill.</p>'
                              if op.get("rol") == "pendiente" else "")
            return (
                f'<div class="panel"><div class="titulo"><h2>{esc(op["ticker"])}</h2>'
                f'<span class="mono">{esc(_subtitulo_velas(op["velas"], tz, ctx["ahora"]))} {marca_pendiente}</span></div>'
                f'{_grafico_velas(op["velas"], op["marcas"], tz, ctx["ahora"], clave=op["ticker"])}'
                f'{nota_velas}{nota_pendiente}{_pie_marcas(op["marcas"], tz, ctx["ahora"])}</div>'
            )

        # Plegables (2026-10-01): abierta solo la entrada más reciente.
        def _hora_op(op):
            return op["marcas"].get("entrada_hora") or datetime.min.replace(tzinfo=timezone.utc)
        reciente = max(ctx["operaciones"], key=_hora_op)

        def _plegable(op):
            m = op["marcas"]
            res = (f'entrada {fmt_dinero(m["entrada_precio"]) if m.get("entrada_precio") is not None else "sin dato"}'
                   f' · stop {fmt_dinero(m["stop"]) if m.get("stop") is not None else "sin dato"}'
                   f' · objetivo {fmt_dinero(m.get("objetivo")) if m.get("objetivo") is not None else "sin dato"}')
            abierto = " open" if op is reciente else ""
            id_ = "vela-" + re.sub(r"[^A-Za-z0-9_-]", "", str(op["ticker"]))
            return (f'<details class="ctl vela-op" id="{id_}"{abierto}><summary><b>{esc(op["ticker"])}</b>'
                    f'<span class="mono">{esc(res)}</span></summary>{_tarjeta(op)}</details>')

        tarjetas = "".join(_plegable(op) for op in ctx["operaciones"])
        if ctx["operaciones_omitidas"]:
            tarjetas += (f'<p class="vacio">Sin graficar por el tope de tickers por corrida: '
                         f'{esc(", ".join(ctx["operaciones_omitidas"]))}.</p>')
        operaciones = f'<div class="operaciones">{tarjetas}</div>'
    elif ctx["hay_alpaca_operaciones"]:
        if ctx.get("pendientes_broker") is None:
            operaciones = '<p class="vacio">Sin posiciones abiertas. Las órdenes pendientes no se pudieron leer.</p>'
        else:
            operaciones = '<p class="vacio">Sin posiciones abiertas ni órdenes pendientes.</p>'
    else:
        operaciones = '<p class="vacio falta">Sin datos: Alpaca no respondió posiciones u órdenes.</p>'

    fuente = ctx.get("fuente_datos")
    # La telemetría dice qué contestó el hunter. El gráfico pide SIP por
    # su cuenta y solo cae a Yahoo si el feed no deja velas de hoy: no se
    # afirma que sean la misma fuente.
    origen_txt = "1 min · SIP, o Yahoo (respaldo) si el feed no alcanza"
    if fuente:
        sub_velas = f"{origen_txt} · el hunter reporta {fuente} · pendiente va marcado"
    else:
        sub_velas = f"{origen_txt} · ruptura, entrada (fill), stop · pendiente va marcado"

    fuente_datos = (f'<span class="pildora">Fuente de datos: {esc(ctx["fuente_datos"])}</span>'
                    if ctx.get("fuente_datos") else "")
    sesion = '<span class="pildora ok">Sesión US abierta</span>' if ctx["en_sesion"] else '<span class="pildora">Sesión US cerrada</span>'
    alpaca = "" if ctx["alpaca_ok"] else '<span class="pildora mal">Alpaca sin conexión</span>'
    persist = ""
    if ctx["persist_fallidos"]:
        ultimo = ctx["persist_fallidos"][-1]
        persist = (f'<span class="pildora mal">Persist fallido ×{len(ctx["persist_fallidos"])} · '
                   f'último {esc(ultimo["hora"])} ({esc(ultimo["motivo"])})</span>')
    ia = ""
    if ctx.get("ia_fallos"):
        ultimo = ctx["ia_fallos"][-1]
        # El saldo es el caso que se fue en silencio el 2026-09-21. Si
        # hoy hubo al menos uno, la píldora lo dice; si no, es el fallo
        # técnico genérico. El motivo viene de fuera y se escapa.
        hay_credito = any(f.get("codigo") == "credito" for f in ctx["ia_fallos"])
        etiqueta = "IA sin crédito" if hay_credito else "IA fallo técnico"
        ia = (f'<span class="pildora mal">{etiqueta} ×{len(ctx["ia_fallos"])} · '
              f'último {esc(ultimo["hora"])} ({esc(ultimo["motivo"])})</span>')

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>Momentum · panel paper</title>
<script>/* Aplica el tema elegido ANTES de pintar, para que el refresco de
cada 60 s no parpadee. Sin elección guardada manda el sistema. */
try{{var _t=localStorage.getItem("tema");if(_t==="dark"||_t==="light")document.documentElement.dataset.theme=_t;}}catch(_e){{}}</script>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Space+Grotesk:wght@400;500;700&display=swap">
<style>{CSS}</style>
</head>
<body data-generado="{int(ctx['ahora'].timestamp())}">
<main>
<header>
  <div class="marca">
    <div class="logo"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 17 9 11 13 15 21 7"/><polyline points="15 7 21 7 21 13"/></svg></div>
    <div><h1>MOMENTUM</h1><div class="sub">hernan-portafolio · buscador → lista de candidatos → ejecutor</div></div>
  </div>
  <div class="pildoras">
    <span class="pildora paper">PAPER · ALPACA</span>{sesion}{alpaca}{persist}{ia}
    <span class="pildora">Act. {_hora(ctx['ahora'], tz)} {esc(etiqueta_tz)}</span>
    <span class="pildora mal" id="panel-viejo" hidden></span>
    <details class="mas"><summary class="pildora">más ▾</summary><div class="pildoras">
      {fuente_datos}<span class="pildora">Solo lectura</span>
      <span class="pildora" title="hora exacta de generación">Generado {_hora(ctx['ahora'], tz, segundos=True)}</span>
      <a class="pildora" href="noticias.html">Noticias leídas</a>
      <button class="pildora" id="tema-toggle" type="button" aria-label="Cambiar entre tema claro y oscuro" title="Cambiar tema claro/oscuro">Tema</button>
    </div></details>
  </div>
</header>
{problemas}
{_html_banda_datos(ctx)}
{_html_anclas()}
{_html_sistema(ctx, etapas)}
<section id="resumen" class="fila c5" aria-label="Cifras clave">{kpis_html}</section>
<section id="riesgo" class="panel" aria-label="Control de riesgo y aprendizaje">
  <div class="titulo"><h2>Control de riesgo y aprendizaje</h2><span class="mono">solo lectura · ajustes en prueba (no se aplican)</span></div>
  {_html_control_aprendizaje(ctx)}
</section>
{_html_broker(ctx).replace('<section ', '<section id="posiciones" ', 1)}
<section id="equity" class="fila c2i" aria-label="Curva de equity">{equity_html}</section>
<section id="velas" class="panel" aria-label="Velas de posiciones abiertas">
  <div class="titulo"><h2>Velas de posiciones abiertas</h2><span class="mono">{sub_velas}</span></div>
  {operaciones}
</section>
<section id="watchlist" class="fila c2" aria-label="Watchlist y latencia">
  <div class="panel"><div class="titulo"><h2>Watchlist actual</h2><span class="mono">{esc(resumen_watch)}generada {_hora(ctx['wl_momento'], tz, ahora=ctx['ahora'])}</span></div>{watch}</div>
  <div class="panel"><div class="titulo"><h2>Latencia</h2><span class="mono">qué tan rápido compra el bot</span></div>
    <details class="explica-mas" id="lat-explica"><summary class="mono">¿Qué es esto?</summary><p class="explica">Minutos entre que el precio rompe (la señal) y que el bot manda la compra. Menos es mejor: si tarda, compra más caro. Cada barra es una compra de hoy; la línea roja es el límite de {fmt_num(ctx['presupuesto'])} min. En gris, el tiempo esperando cupo: no cuenta como tarde.</p></details>
    {_grafico_latencia(ctx)}
    <div class="stats"><div><span class="mono">Lo típico</span><b>{fmt_num(ctx['lat_mediana'], ' min')}</b></div><div><span class="mono">9 de cada 10</span><b>{'—' if ctx['lat_p90'] is None else '≤ ' + fmt_num(ctx['lat_p90'], ' min')}</b></div><div><span class="mono">Llegaron tarde</span><b class="{'neg' if ctx['lat_fuera'] else 'pos'}">{fmt_num(ctx['lat_fuera'])}</b></div></div>
    {_html_notas_latencia(ctx)}{_veredicto_latencia(ctx)}
  </div>
</section>
<section id="ejecucion" class="fila c3" aria-label="Ejecución y límites">
  <div class="panel oscuro"><div class="titulo"><h2>Stream de ejecución</h2><span class="sub">órdenes paper de hoy</span></div>{stream}</div>
  <div class="panel"><div class="titulo"><h2>Dudas del ejecutor</h2><span class="mono">entradas que la IA rechazó</span></div>{dudas}</div>
  <div class="panel"><div class="titulo"><h2>Límites de riesgo</h2><span class="mono" title="fail-closed">si falta un dato, no opera</span></div>{riesgo}</div>
</section>
<section id="historial" class="panel" aria-label="Trades de días anteriores">
  <div class="titulo"><h2>Trades de días anteriores</h2><span class="mono">cerrados · hora Monterrey (UTC−6) · fuente: memoria de trades (fills paper, solo lectura)</span></div>
  {_html_historial_trades(ctx)}
</section>
</main>
<script>/* Panel viejo (2026-09-29): el HTML se regenera cada minuto y el
navegador lo recarga cada 60 s. Si el generador se para pero el servidor
HTTP sigue, la página recargada es la MISMA copia vieja y todo se ve
"OK". Se compara la hora de generación con el reloj del navegador. */
(function(){{
  var gen=parseInt(document.body.dataset.generado||"0",10)*1000;
  var p=document.getElementById("panel-viejo");if(!gen||!p)return;
  function revisa(){{
    var min=Math.floor((Date.now()-gen)/60000);
    if(min>=3){{p.hidden=false;p.textContent="Panel sin regenerarse hace "+min+" min";}}
  }}
  revisa();setInterval(revisa,30000);
}})();</script>
<script>/* Botón de tema: alterna claro/oscuro y guarda la elección en el
navegador (por dispositivo). Sin elección previa parte de lo que pide el
sistema. Todo entre try por si el navegador bloquea el almacenamiento. */
(function(){{
  var b=document.getElementById("tema-toggle");if(!b)return;
  var root=document.documentElement;
  function actual(){{
    var t=root.dataset.theme;
    if(t==="dark"||t==="light")return t;
    try{{return window.matchMedia&&window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light";}}catch(e){{return "light";}}
  }}
  function pinta(){{b.textContent="Tema: "+(actual()==="dark"?"Oscuro":"Claro");}}
  pinta();
  b.addEventListener("click",function(){{
    var nuevo=actual()==="dark"?"light":"dark";
    root.dataset.theme=nuevo;
    try{{localStorage.setItem("tema",nuevo);}}catch(e){{}}
    pinta();
  }});
}})();</script>
<script>/* Plegables (2026-10-01): el panel se recarga cada 60 s; se recuerda
qué <details> abrió el usuario (por id, en este navegador). */
(function(){{
  var k="det-abiertos",m={{}};
  try{{m=JSON.parse(localStorage.getItem(k)||"{{}}")||{{}};}}catch(e){{m={{}};}}
  document.querySelectorAll("details[id]").forEach(function(d){{
    if(!d.dataset.forzar&&Object.prototype.hasOwnProperty.call(m,d.id))d.open=!!m[d.id];
    d.addEventListener("toggle",function(){{m[d.id]=d.open;try{{localStorage.setItem(k,JSON.stringify(m));}}catch(e){{}}}});
  }});
}})();</script>
</body>
</html>"""


def escribir(html_texto: str, salida: Path) -> Path:
    salida.mkdir(parents=True, exist_ok=True)
    destino = salida / "index.html"
    temporal = salida / ".index.html.tmp"
    temporal.write_text(html_texto, encoding="utf-8")
    os.replace(temporal, destino)  # atómico: el navegador nunca ve un archivo a medias
    return destino


def main() -> int:
    cfg = cargar_config()
    ctx = construir(datetime.now(timezone.utc), cfg)
    try:
        destino = escribir(render(ctx), cfg["salida"])
    except OSError as exc:
        print(f"No se pudo escribir el panel: {exc}", file=sys.stderr)
        return 1
    for p in ctx["problemas"]:
        print(f"aviso: {p}", file=sys.stderr)
    print(f"Panel escrito en {destino}")
    # Página aparte: si falla, el panel principal ya quedó escrito y el
    # código de salida no cambia.
    try:
        from dashboard import noticias
        ruta = cfg.get("noticias_leidas")
        if ruta is not None:
            noticias.generar(Path(ruta), cfg["salida"], ctx["ahora"], cfg["tz"])
    except Exception as exc:  # noqa: BLE001
        print(f"aviso: no se pudo escribir noticias.html ({type(exc).__name__})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
