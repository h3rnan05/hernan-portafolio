"""Halt y banda LULD, leídos solo del feed de DATOS.

Alpaca documenta el halt y el LULD en el stream de datos
(`wss://stream.data.alpaca.markets/v2/sip`, canales `statuses` y
`lulds`: mensaje `T=s` y `T=l`). Ese socket lo tiene que abrir UN
proceso. El PR #207 (`momentum_hunter.data.sip_stream`) ya es el
dueño de la conexión de barras y todavía no está en main: este
módulo NO abre otro websocket (el plan corta en una conexión por
endpoint; la segunda recibe 406 y no reemplaza a la primera).

Mientras ese proceso no escriba statuses y lulds, la fuente que
funciona contra main es el REST de snapshots
(`GET /v2/stocks/snapshots` en `https://data.alpaca.markets`), que
trae el último trade y la última quote con sus condiciones (`c`).
El catálogo de esas letras es `GET /v2/stocks/meta/conditions/{trade|quote}`
(códigos SIP, no un endpoint de trading). Si el almacén
`$MOMENTUM_ESTADO_DIR/sip_stream/` (default
`/var/lib/momentum/estado/sip_stream/`) trae eventos frescos, se
prefieren: son el canal que de verdad dice halt y reanudación.

Un campo ausente no es "no hay halt". Una quote regular vieja
tampoco: durante un halt la cinta se queda en el último print.

Códigos que SÍ se interpretan (el resto no se promueve a "operando"):

  Status `T=s` (doc de Alpaca, real-time stock data):
    Tape C/O: H halt, P pausa de volatilidad, T reanudación de
    trading, Q solo cotización (no es reanudación: no se puede
    comprar). Tape A/B: 2 halt, 3 reanudación, F es aviso LULD
    (pausa solo si el reason es M / LUDP / LUDS).
    Reasons de pausa o circuit breaker: T1 T2 T5 T6 T8 T12 H4 H9
    H10 H11 01 IPO1 M1 M2 LUDP LUDS MWC0-3, y en CTA M y 1/2/3.

  LULD `T=l`: `u`/`d` son las bandas, `i` es el indicador SIP
    (A apertura, B intradía, C restated, D suspendida durante
    halt o pausa, E reapertura, F fuera de horario). D es halt.
    Una banda intradía no es un halt: casi todo el universo la
    recibe todo el día. Sí restringe la entrada si el precio
    pedido queda fuera de una banda fresca.

  Quote del snapshot, códigos SIP que el meta de Alpaca publica:
    Tape C/O: L closed quote y Z no open/no resume = halt.
    Tape A/B: L closed market maker = halt; E/F/U slow quote por
    LRP o gap = estado LULD (no es un print normal).
    Regular: R (y espacio en tape A/B). Sin `c` o con `c` vacío
    no es regular.

Este módulo no coloca, no cancela y no importa el cliente de
trading. La decisión de no mandar la orden vive en el ejecutor.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from momentum_hunter.data.alpaca_datos import AlpacaProvider, ErrorDatosAlpaca

# Misma ventana que el latido del stream de barras (PR #207): por
# encima de esto el dato ya no dice cómo está el símbolo AHORA.
# No es un umbral de la estrategia.
FRESCURA_MAX_S = 180.0
# Un halt del viernes sigue siendo el último status el lunes a la
# apertura. Tres días de NY alcanzan para eso; más atrás el snapshot
# sigue viendo la quote cerrada si la cinta no reanudó.
_DIAS_EVENTOS = 3

ENV_ESTADO = "MOMENTUM_ESTADO_DIR"
ESTADO_DEFAULT = Path("/var/lib/momentum/estado")

# Status codes. Las cadenas son las de la doc, no números inventados.
_HALT_SC = {
    "C": {"H", "P"},
    "O": {"H", "P"},
    "A": {"2"},
    "B": {"2"},
}
_RESUME_SC = {"C": {"T"}, "O": {"T"}, "A": {"3"}, "B": {"3"}}
_SOLO_COTIZACION = {"Q"}
_LULD_SC = {"F"}
_HALT_RC = frozenset({
    "T1", "T2", "T5", "T6", "T8", "T12", "H4", "H9", "H10", "H11",
    "01", "IPO1", "M1", "M2", "LUDP", "LUDS", "MWC0", "MWC1", "MWC2", "MWC3",
    "M", "1", "2", "3",
})
_PAUSA_LULD_RC = frozenset({"M", "LUDP", "LUDS"})
# Indicador de banda. D = la banda está suspendida porque hay halt o pausa.
_LULD_HALT_I = "D"
_LULD_BANDA_I = frozenset({"A", "B", "C", "E"})

_HALT_QUOTE = {"C": frozenset({"L", "Z"}), "O": frozenset({"L", "Z"}), "A": frozenset({"L"}), "B": frozenset({"L"})}
_LULD_QUOTE = {"A": frozenset({"E", "F", "U"}), "B": frozenset({"E", "F", "U"})}
_REGULAR_QUOTE = {
    "C": frozenset({"R"}), "O": frozenset({"R"}),
    "A": frozenset({"R", " "}), "B": frozenset({"R", " "}),
}


def directorio_estado(base: Path | None = None) -> Path:
    if base is not None:
        return Path(base)
    raw = os.environ.get(ENV_ESTADO, "").strip()
    return Path(raw) if raw else ESTADO_DEFAULT


def directorio_stream(base: Path | None = None) -> Path:
    return directorio_estado(base) / "sip_stream"


def _momento(valor: object) -> datetime | None:
    """Timestamp con zona a UTC. Sin zona no se asume UTC: un reloj
    ambiguo no ordena un halt contra una reanudación."""
    if isinstance(valor, datetime):
        if valor.tzinfo is None:
            return None
        return valor.astimezone(UTC)
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


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _numero(valor: object) -> float | None:
    if isinstance(valor, bool) or valor is None:
        return None
    try:
        n = float(valor)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if n != n or n in (float("inf"), float("-inf")):
        return None
    return n


def _codigo(valor: object) -> str | None:
    """Un código SIP. El espacio es la quote regular de tape A/B:
    no se recorta. Una cadena vacía no es un código."""
    if isinstance(valor, bool) or valor is None:
        return None
    if isinstance(valor, int):
        return str(valor)
    if not isinstance(valor, str) or valor == "":
        return None
    if valor == " ":
        return " "
    s = valor.strip()
    return s if s else None


def _texto(valor: object) -> str | None:
    if not isinstance(valor, str):
        return None
    s = valor.strip()
    return s if s else None


def _fresco(momento: datetime | None, ahora: datetime) -> bool:
    if momento is None:
        return False
    return (ahora - momento).total_seconds() <= FRESCURA_MAX_S


@dataclass(frozen=True)
class LecturaHalt:
    """Lo que se puede afirmar de un símbolo. `en_halt is None` es
    desconocido: no es False. `restringe_luld is True` es una banda
    que no deja pasar la entrada (fuera de banda, slow quote, o
    indicador D). `fresco` dice si la fuente sigue viva; un "operando"
    viejo no se sigue creyendo."""

    ticker: str
    situacion: str
    en_halt: bool | None
    restringe_luld: bool | None
    fresco: bool
    fuente: str
    evento_en: str | None
    halt_id: str | None
    limit_up: float | None
    limit_down: float | None
    indicador_luld: str | None
    detalle: str

    @property
    def debe_bloquear(self) -> bool:
        """True si en enforce no se puede mandar la entrada.

        Halt confirmado, dato ausente, dato viejo, o LULD que restringe.
        Un False de `en_halt` solo deja pasar si además está fresco y
        la banda no lo saca."""
        if self.en_halt is True:
            return True
        if self.en_halt is None:
            return True
        if not self.fresco:
            return True
        if self.restringe_luld is True:
            return True
        return False


def desconocido(ticker: str, *, fuente: str, detalle: str) -> LecturaHalt:
    return LecturaHalt(
        ticker=ticker, situacion="desconocido", en_halt=None, restringe_luld=None,
        fresco=False, fuente=fuente, evento_en=None, halt_id=None,
        limit_up=None, limit_down=None, indicador_luld=None, detalle=detalle,
    )


def _condiciones(obj: object) -> list[str] | None:
    """None si `c` no vino o no es una lista de códigos. Lista vacía
    es 'vino vacío', que tampoco es un print regular."""
    if not isinstance(obj, dict) or "c" not in obj:
        return None
    crudo = obj.get("c")
    if not isinstance(crudo, list):
        return None
    out: list[str] = []
    for item in crudo:
        codigo = _codigo(item)
        if codigo is None:
            return None
        out.append(codigo)
    return out


def _clases_quote(condiciones: list[str], tape: str | None) -> str:
    """`halt`, `luld`, `regular` o `otro`. `otro` no se promociona."""
    codes = set(condiciones)
    if not codes:
        return "otro"
    halt = _HALT_QUOTE.get(tape or "", frozenset({"L"}))
    # L es halt en las dos cintas. Z solo en C/O: sin tape no se
    # adivina, y cae en `otro` (el enforce igual no entra).
    if tape in _HALT_QUOTE and codes & _HALT_QUOTE[tape]:
        return "halt"
    if tape is None and "L" in codes:
        return "halt"
    luld = _LULD_QUOTE.get(tape or "")
    if luld and codes & luld:
        return "luld"
    regular = _REGULAR_QUOTE.get(tape or "", frozenset({"R"}))
    if codes <= regular:
        return "regular"
    return "otro"


def interpretar_snapshot(
    ticker: str,
    crudo: object,
    ahora: datetime,
    precio_entrada: float | None = None,
) -> LecturaHalt:
    """Una foto REST. Sin cuerpo, sin quote o sin condiciones no es
    'está operando'."""
    if not isinstance(crudo, dict):
        return desconocido(ticker, fuente="snapshot", detalle="snapshot ilegible")
    quote = crudo.get("latestQuote") if isinstance(crudo.get("latestQuote"), dict) else None
    trade = crudo.get("latestTrade") if isinstance(crudo.get("latestTrade"), dict) else None
    if quote is None and trade is None:
        return desconocido(ticker, fuente="snapshot", detalle="snapshot sin trade ni quote")
    q_c = _condiciones(quote) if quote is not None else None
    q_t = _momento(quote.get("t")) if quote is not None else None
    tape = _codigo(quote.get("z")) if quote is not None else None
    if isinstance(tape, str):
        tape = tape.upper()
    if quote is None or q_c is None:
        return desconocido(ticker, fuente="snapshot", detalle="quote sin condiciones")
    clase = _clases_quote(q_c, tape)
    evento = _iso(q_t)
    if clase == "halt":
        halt_id = f"quote:{evento or 'sin_reloj'}:{''.join(sorted(set(q_c)))}"
        return LecturaHalt(
            ticker=ticker, situacion="halt", en_halt=True, restringe_luld=None,
            fresco=_fresco(q_t, ahora), fuente="snapshot", evento_en=evento, halt_id=halt_id,
            limit_up=None, limit_down=None, indicador_luld=None,
            detalle=f"quote {' '.join(q_c)} cinta {tape or '?'}",
        )
    if clase == "luld":
        return LecturaHalt(
            ticker=ticker, situacion="luld", en_halt=None, restringe_luld=True,
            fresco=_fresco(q_t, ahora), fuente="snapshot", evento_en=evento, halt_id=None,
            limit_up=None, limit_down=None, indicador_luld=None,
            detalle=f"quote LULD {' '.join(q_c)} cinta {tape or '?'}",
        )
    t_c = _condiciones(trade) if trade is not None else None
    t_m = _momento(trade.get("t")) if trade is not None else None
    if clase != "regular" or t_c is None or len(t_c) == 0 or q_t is None or t_m is None:
        return desconocido(
            ticker, fuente="snapshot",
            detalle="snapshot sin print regular en los dos lados",
        )
    fresco = _fresco(q_t, ahora) and _fresco(t_m, ahora)
    if not fresco:
        return LecturaHalt(
            ticker=ticker, situacion="viejo", en_halt=None, restringe_luld=None,
            fresco=False, fuente="snapshot", evento_en=_iso(min(q_t, t_m)), halt_id=None,
            limit_up=None, limit_down=None, indicador_luld=None,
            detalle="el último print regular ya no es fresco",
        )
    # El precio de entrada no se compara acá: el snapshot no trae la
    # banda. `precio_entrada` queda para el que fusiona con un LULD.
    _ = precio_entrada
    return LecturaHalt(
        ticker=ticker, situacion="operando", en_halt=False, restringe_luld=None,
        fresco=True, fuente="snapshot", evento_en=_iso(max(q_t, t_m)), halt_id=None,
        limit_up=None, limit_down=None, indicador_luld=None,
        detalle="trade y quote regulares y frescos",
    )


def _es_status(msg: dict) -> bool:
    tipo = msg.get("T")
    marca = msg.get("tipo")
    return tipo == "s" or marca == "status"


def _es_luld(msg: dict) -> bool:
    tipo = msg.get("T")
    marca = msg.get("tipo")
    return tipo == "l" or marca == "luld"


def _status_efecto(msg: dict) -> str | None:
    """`halt`, `resume`, `luld` o None si el mensaje no decide.

    Sin `sc` no se inventa el efecto. Q no reanuda el trading."""
    sc = _codigo(msg.get("sc"))
    if sc is None:
        return None
    rc = _codigo(msg.get("rc")) or ""
    tape = _codigo(msg.get("z"))
    tape = tape.upper() if isinstance(tape, str) else None
    if sc in _SOLO_COTIZACION:
        return "halt"
    if sc in _LULD_SC:
        return "halt" if rc in _PAUSA_LULD_RC else "luld"
    halt_sc = _HALT_SC.get(tape or "")
    if halt_sc and sc in halt_sc:
        return "halt"
    if tape is None and sc in {"H", "P", "2"}:
        # H/P son UTP y 2 es CTA: no se pisan entre cintas.
        return "halt"
    if rc in _HALT_RC and sc not in {"T", "3"}:
        return "halt"
    resume = _RESUME_SC.get(tape or "")
    if resume and sc in resume and rc not in _HALT_RC:
        return "resume"
    if tape is None and sc in {"T", "3"} and rc not in _HALT_RC:
        return "resume"
    return None


def _aplicar_banda(base: LecturaHalt, up: float | None, down: float | None,
                   indicador: str | None, cuando: datetime | None, ahora: datetime,
                   precio: float | None, detalle: str) -> LecturaHalt:
    """Pega una banda encima de otra lectura. No inventa el lado que
    falta, y una banda vieja no se usa para decir que el precio cabe."""
    restringe = base.restringe_luld
    if indicador == _LULD_HALT_I:
        return LecturaHalt(
            ticker=base.ticker, situacion="halt", en_halt=True, restringe_luld=True,
            fresco=base.fresco and _fresco(cuando, ahora), fuente=base.fuente,
            evento_en=_iso(cuando) or base.evento_en,
            halt_id=base.halt_id or f"luld:{_iso(cuando) or 'sin_reloj'}",
            limit_up=up if up is not None else base.limit_up,
            limit_down=down if down is not None else base.limit_down,
            indicador_luld=indicador, detalle=detalle or base.detalle,
        )
    if up is not None and down is not None and precio is not None and _fresco(cuando, ahora):
        if precio > up or precio < down:
            restringe = True
        elif restringe is None:
            restringe = False
    situacion = base.situacion
    if restringe is True and situacion in {"operando", "reanudado"}:
        situacion = "luld"
    elif situacion == "desconocido" and (up is not None or down is not None):
        situacion = "luld"
    return LecturaHalt(
        ticker=base.ticker, situacion=situacion, en_halt=base.en_halt,
        restringe_luld=restringe, fresco=base.fresco, fuente=base.fuente,
        evento_en=base.evento_en, halt_id=base.halt_id,
        limit_up=up if up is not None else base.limit_up,
        limit_down=down if down is not None else base.limit_down,
        indicador_luld=indicador if indicador is not None else base.indicador_luld,
        detalle=base.detalle if base.situacion == "halt" else (detalle or base.detalle),
    )


def reducir_eventos(
    ticker: str,
    eventos: list[dict],
    ahora: datetime,
    *,
    stream_fresco: bool,
    precio_entrada: float | None = None,
) -> LecturaHalt | None:
    """El último status con reloj gana. Sin ningún evento usable,
    None: el caller sigue por el snapshot. Una línea ilegible no
    llega acá (el lector ya la saltó) y no cuenta como reanudación."""
    status: list[tuple[datetime, str, dict]] = []
    lulds: list[tuple[datetime, dict]] = []
    for msg in eventos:
        if not isinstance(msg, dict):
            continue
        cuando = _momento(msg.get("t"))
        if cuando is None:
            continue
        if _es_status(msg):
            efecto = _status_efecto(msg)
            if efecto is not None:
                status.append((cuando, efecto, msg))
        elif _es_luld(msg):
            lulds.append((cuando, msg))
    if not status and not lulds:
        return None
    # Status y LULD en el orden del reloj. Aplicar la banda al final
    # reabriría un halt que una reanudación posterior ya cerró.
    linea: list[tuple[datetime, str, object]] = []
    for cuando, efecto, msg in status:
        linea.append((cuando, "status", (efecto, msg)))
    for cuando, msg in lulds:
        linea.append((cuando, "luld", msg))
    linea.sort(key=lambda item: item[0])

    en_halt: bool | None = None
    halt_id: str | None = None
    evento_en: datetime | None = None
    detalle = ""
    limit_up: float | None = None
    limit_down: float | None = None
    indicador: str | None = None
    # La pausa que dice el status no sobrevive a una reanudación.
    # El precio fuera de la última banda sí: la banda sigue publicada.
    restringe_status: bool | None = None
    restringe_precio: bool | None = None

    for cuando, kind, payload in linea:
        if kind == "status":
            efecto, msg = payload  # type: ignore[misc]
            sc = _codigo(msg.get("sc")) or "?"
            rc = _codigo(msg.get("rc")) or ""
            if efecto == "halt":
                if en_halt is not True:
                    halt_id = f"status:{_iso(cuando)}"
                en_halt = True
                evento_en = cuando
                detalle = f"status {sc}" + (f"/{rc}" if rc else "")
            elif efecto == "resume":
                en_halt = False
                halt_id = None
                evento_en = cuando
                detalle = f"reanudación {sc}"
                restringe_status = None
                if indicador == _LULD_HALT_I:
                    indicador = None
            elif efecto == "luld" and en_halt is not True:
                restringe_status = True
                evento_en = cuando
                detalle = f"status LULD {sc}"
            continue
        msg = payload  # type: ignore[assignment]
        up = _numero(msg.get("u"))
        down = _numero(msg.get("d"))
        ind = _codigo(msg.get("i"))
        if up is not None:
            limit_up = up
        if down is not None:
            limit_down = down
        if ind is not None:
            indicador = ind
        if ind == _LULD_HALT_I:
            if en_halt is not True:
                halt_id = f"luld:{_iso(cuando)}"
            en_halt = True
            evento_en = cuando
            detalle = "LULD suspendida (i=D)"
            restringe_status = True
        elif limit_up is not None and limit_down is not None and precio_entrada is not None and _fresco(cuando, ahora):
            fuera = precio_entrada > limit_up or precio_entrada < limit_down
            restringe_precio = True if fuera else False
        if evento_en is None:
            evento_en = cuando
            detalle = detalle or f"LULD i={ind or '?'}"

    if en_halt is None and limit_up is None and limit_down is None and not detalle:
        return None
    if restringe_status is True or restringe_precio is True:
        restringe: bool | None = True
    elif restringe_precio is False or restringe_status is False:
        restringe = False
    else:
        restringe = None
    # El status es event-driven: si el socket sigue vivo, una
    # reanudación de hace 20 minutos sigue vigente (un halt nuevo
    # habría llegado). Los 180 s miden el latido y la banda, no la
    # edad del último status. Un socket caído no convierte esa
    # reanudación en "sigue operando": pasa a viejo.
    if en_halt is True:
        situacion = "halt"
        en_halt_out: bool | None = True
        fresco_out = stream_fresco
    elif en_halt is False and stream_fresco:
        situacion = "luld" if restringe is True else "reanudado"
        en_halt_out = False
        fresco_out = True
    elif en_halt is False:
        situacion = "viejo"
        en_halt_out = None
        fresco_out = False
        detalle = "la reanudación ya no es fresca"
        halt_id = None
    elif restringe is True or limit_up is not None or limit_down is not None:
        situacion = "luld"
        en_halt_out = None
        # Sin status, la banda solo es usable si el socket late y o
        # bien el precio ya se comparó contra una banda fresca, o el
        # propio mensaje LULD todavía entra en la ventana.
        fresco_out = stream_fresco and (
            restringe_precio is not None or _fresco(evento_en, ahora)
        )
    else:
        situacion = "desconocido"
        en_halt_out = None
        fresco_out = stream_fresco and _fresco(evento_en, ahora)
    return LecturaHalt(
        ticker=ticker, situacion=situacion, en_halt=en_halt_out, restringe_luld=restringe,
        fresco=fresco_out, fuente="stream", evento_en=_iso(evento_en), halt_id=halt_id,
        limit_up=limit_up, limit_down=limit_down, indicador_luld=indicador, detalle=detalle,
    )


def _stream_esta_fresco(estado: dict | None, ahora: datetime) -> bool:
    """El `estado.json` del dueño del socket. Versión distinta, caído,
    con hueco o sin latido: no está fresco. No se rellena con False
    de 'conectado' si el archivo no existe: eso lo decide el caller
    al no tener estado."""
    if not isinstance(estado, dict) or estado.get("version") != 1:
        return False
    if estado.get("conectado") is not True or estado.get("hueco_abierto") is True:
        return False
    hb = _momento(estado.get("ultimo_heartbeat_en"))
    return _fresco(hb, ahora)


def _leer_estado_stream(directorio: Path) -> dict | None:
    path = directorio / "estado.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _eventos_de(directorio: Path, ahora: datetime) -> tuple[dict[str, list[dict]], int]:
    """Eventos por símbolo, y cuántas líneas no se pudieron leer.

    Una línea rota no se convierte en reanudación ni en halt."""
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    malas = 0
    por: dict[str, list[dict]] = {}
    if not directorio.is_dir():
        return {}, 0
    dia = ahora.astimezone(ny).date()
    for i in range(_DIAS_EVENTOS):
        fecha = (dia - timedelta(days=i)).isoformat()
        path = directorio / "eventos" / f"{fecha}.jsonl"
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
            if not isinstance(obj, dict):
                malas += 1
                continue
            if not (_es_status(obj) or _es_luld(obj)):
                continue
            simbolo = _texto(obj.get("S"))
            if simbolo is None:
                malas += 1
                continue
            por.setdefault(simbolo.upper(), []).append(obj)
    return por, malas


def leer_stream(
    tickers: list[str],
    ahora: datetime,
    *,
    directorio: Path | None = None,
    precios: dict[str, float | None] | None = None,
) -> dict[str, LecturaHalt]:
    """Opinión del almacén SIP, solo para los símbolos que tienen un
    status o un LULD usable. Si el directorio no está, el mapa sale
    vacío y el caller usa el snapshot: no se crea el almacén."""
    raiz = directorio if directorio is not None else directorio_stream()
    if not raiz.is_dir():
        return {}
    estado = _leer_estado_stream(raiz)
    fresco = _stream_esta_fresco(estado, ahora)
    por, _malas = _eventos_de(raiz, ahora)
    precios = precios or {}
    out: dict[str, LecturaHalt] = {}
    for ticker in tickers:
        if not isinstance(ticker, str) or not ticker.strip():
            continue
        clave = ticker.strip().upper()
        eventos = por.get(clave)
        if not eventos:
            continue
        lectura = reducir_eventos(
            clave, eventos, ahora, stream_fresco=fresco,
            precio_entrada=precios.get(ticker) if ticker in precios else precios.get(clave),
        )
        if lectura is not None:
            out[clave] = lectura
    return out


def _posterior(a: str | None, b: str | None) -> bool:
    """True si `a` es un instante estrictamente posterior a `b`.
    Si falta alguno, no se afirma el orden."""
    da, db = _momento(a), _momento(b)
    if da is None or db is None:
        return False
    return da > db


def _con_banda_de(base: LecturaHalt, donante: LecturaHalt | None, ahora: datetime,
                  precio: float | None) -> LecturaHalt:
    """Si el que ganó el status no trae banda y el otro sí, copia los
    precios. No copia el indicador D: ese ya es un halt, y si la base
    ganó es porque ese halt no manda."""
    if donante is None or donante is base:
        return base
    if base.limit_up is not None or base.limit_down is not None:
        return base
    if donante.limit_up is None and donante.limit_down is None:
        return base
    indicador = donante.indicador_luld if donante.indicador_luld != _LULD_HALT_I else None
    return _aplicar_banda(
        base, donante.limit_up, donante.limit_down, indicador,
        _momento(donante.evento_en), ahora, precio, donante.detalle,
    )


def fusionar(
    stream: LecturaHalt | None,
    snap: LecturaHalt | None,
    ticker: str,
    ahora: datetime,
    precio_entrada: float | None = None,
) -> LecturaHalt:
    """El status fresco manda. Un halt que ya vimos no lo borra un
    print más viejo. Un print regular fresco y POSTERIOR sí puede
    cubrir un halt cuyo socket ya no late: si no, un stream caído
    dejaría el halt pegado después de la reanudación que el REST sí
    vio. Un snapshot desconocido no pisa una lectura del stream.
    Sin ninguna de las dos, desconocido."""
    if stream is None and snap is None:
        return desconocido(ticker, fuente="ausente", detalle="sin stream y sin snapshot")
    if stream is not None and stream.en_halt is True:
        if (
            not stream.fresco and snap is not None and snap.en_halt is False and snap.fresco
            and _posterior(snap.evento_en, stream.evento_en)
        ):
            base = snap
            donante = stream
        else:
            base = stream
            donante = snap
    elif stream is not None and stream.en_halt is False and stream.fresco:
        # Una quote de halt MÁS NUEVA que la reanudación no se ignora:
        # el status puede haber quedado atrás aunque el socket siga vivo.
        if (
            snap is not None and snap.en_halt is True
            and _posterior(snap.evento_en, stream.evento_en)
        ):
            base = snap
            donante = stream
        else:
            base = stream
            donante = snap
    elif snap is not None and snap.situacion != "desconocido":
        base = snap
        donante = stream
    elif stream is not None:
        base = stream
        donante = None
    else:
        base = snap if snap is not None else desconocido(
            ticker, fuente="ausente", detalle="sin lectura",
        )
        donante = None
    return _con_banda_de(base, donante, ahora, precio_entrada)


def consultar(
    tickers: list[str],
    ahora: datetime,
    precios: dict[str, float | None] | None = None,
    *,
    directorio: Path | None = None,
    traer=None,
) -> dict[str, LecturaHalt]:
    """Una lectura por ticker. El que no se pudo leer sale
    `desconocido`, nunca `operando`. `traer` devuelve el mapa crudo
    de snapshots o None si el ciclo de datos no respondió."""
    pedidos = [t.strip().upper() for t in tickers if isinstance(t, str) and t.strip()]
    precios = precios or {}
    precios_u = {k.strip().upper(): v for k, v in precios.items() if isinstance(k, str)}
    stream = leer_stream(pedidos, ahora, directorio=directorio, precios=precios_u)
    crudos: dict | None
    if traer is None:
        def traer(lista: list[str]):
            try:
                # Sin respaldo IEX: las condiciones/cinta de la quote
                # que leen los halts son del SIP. Un 403 sigue siendo
                # `desconocido`, como antes, nunca `operando` por IEX.
                return AlpacaProvider(respaldo_iex=False).snapshots_crudos(lista)
            except ErrorDatosAlpaca:
                return None
    try:
        crudos = traer(pedidos) if pedidos else {}
    except ErrorDatosAlpaca:
        crudos = None
    except Exception:
        # Un fallo del transporte no es un universo sin halts.
        crudos = None
    if crudos is not None and not isinstance(crudos, dict):
        crudos = None
    out: dict[str, LecturaHalt] = {}
    for ticker in pedidos:
        snap = None
        if crudos is None:
            snap = desconocido(ticker, fuente="ausente", detalle="el snapshot no respondió")
        elif ticker not in crudos:
            snap = desconocido(ticker, fuente="snapshot", detalle="el snapshot no trajo el símbolo")
        else:
            snap = interpretar_snapshot(ticker, crudos.get(ticker), ahora, precios_u.get(ticker))
        # Si el stream ya cubre el símbolo, el snapshot ausente no
        # pisa un halt: fusionar se queda con el stream.
        if crudos is None and ticker in stream:
            snap = None
        out[ticker] = fusionar(
            stream.get(ticker), snap, ticker, ahora, precios_u.get(ticker),
        )
    return out
