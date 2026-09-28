"""B3. Descarga offline de barras SIP y diff contra Yahoo.

No lo llama ningún timer ni el escaneo. Es un CLI para armar un almacén
local (`--desde` / `--hasta`; el origen por omisión es 2016-01-01, que es
desde cuando el histórico SIP está pedido) y comparar después con el
chart de Yahoo. Ajuste `split`, feed `sip`, host de datos únicamente.

Una vela a la que le falte OHLC o volumen no se guarda y no se convierte
en ceros. Si Yahoo está en pausa por un 429, el diff dice que ese lado
no está disponible.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from momentum_hunter.data.provider import PausaYahoo

from shadow_alpaca.cliente import ClienteDatos, ErrorDatos
from shadow_alpaca.jsonl_log import exigir_directorio_aislado
from shadow_alpaca.numeros import numero

log = logging.getLogger("shadow_alpaca.historia")

NY = ZoneInfo("America/New_York")
DESDE_DEFECTO = date(2016, 1, 1)
RUTA_BARRAS = "/v2/stocks/bars"
AJUSTE = "split"
FEED = "sip"
LIMITE_PAGINA = 10_000
MAX_PAGINAS = 500
CHART_YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{t}"
TIMEFRAMES = {"1Day": "1Day", "1Min": "1Min"}
INTERVALO_YAHOO = {"1Day": "1d", "1Min": "1m"}


def _momento(valor: object) -> datetime | None:
    if not isinstance(valor, str) or not valor:
        return None
    try:
        dt = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(UTC)


def barra_de_alpaca(cruda: object) -> dict | None:
    """Una vela completa, o None si falta el tiempo o algún OHLCV.

    El volumen ausente tira la vela entera. No se escribe un 0 para
    'seguir teniendo la fila'.
    """
    if not isinstance(cruda, dict):
        return None
    momento = _momento(cruda.get("t"))
    o, h, lo, c, v = (
        numero(cruda.get("o")), numero(cruda.get("h")), numero(cruda.get("l")),
        numero(cruda.get("c")), numero(cruda.get("v")),
    )
    if momento is None or None in (o, h, lo, c, v):
        return None
    return {
        "t": momento.isoformat(timespec="seconds"),
        "o": o, "h": h, "l": lo, "c": c, "v": v,
    }


def _clave_diaria(momento: datetime) -> str:
    return momento.astimezone(NY).date().isoformat()


def _clave_minuto(momento: datetime) -> str:
    m = momento.astimezone(UTC).replace(second=0, microsecond=0)
    return m.isoformat(timespec="minutes")


def clave_de(barra: dict, timeframe: str) -> str | None:
    momento = _momento(barra.get("t"))
    if momento is None:
        return None
    if timeframe == "1Day":
        return _clave_diaria(momento)
    return _clave_minuto(momento)


def parsear_pagina_barras(cuerpo: object, simbolo: str) -> tuple[list[dict], int]:
    """`(velas, descartadas)`. Si `bars` no es un objeto, no es 'cero velas'."""
    if not isinstance(cuerpo, dict) or "bars" not in cuerpo:
        raise ErrorDatos("bars_ausente")
    bars = cuerpo.get("bars")
    if not isinstance(bars, dict):
        raise ErrorDatos("bars_ilegible")
    serie = bars.get(simbolo)
    if serie is None:
        # El símbolo no vino en esta página. Puede ser un hueco real del
        # feed (no hay velas) o una página que solo trae el token. No es
        # un error de forma: se distingue de `bars` ausente.
        return [], 0
    if not isinstance(serie, list):
        raise ErrorDatos("bars_ilegible")
    out: list[dict] = []
    descartadas = 0
    for cruda in serie:
        barra = barra_de_alpaca(cruda)
        if barra is None:
            descartadas += 1
        else:
            out.append(barra)
    return out, descartadas


def descargar_simbolo(
    cliente: ClienteDatos,
    simbolo: str,
    timeframe: str,
    desde: date,
    hasta: date,
    almacen: Path,
) -> dict:
    if timeframe not in TIMEFRAMES:
        raise ErrorDatos("intervalo")
    if hasta < desde:
        raise ErrorDatos("rango")
    inicio = datetime(desde.year, desde.month, desde.day, tzinfo=UTC)
    # `hasta` inclusive: el fin es el día siguiente a medianoche UTC.
    fin = datetime(hasta.year, hasta.month, hasta.day, tzinfo=UTC) + timedelta(days=1)
    params = {
        "symbols": simbolo,
        "timeframe": TIMEFRAMES[timeframe],
        "start": inicio.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "end": fin.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "limit": LIMITE_PAGINA,
        "adjustment": AJUSTE,
        "feed": FEED,
        "sort": "asc",
    }
    paginas, truncado = cliente.paginas(RUTA_BARRAS, params, MAX_PAGINAS)
    velas: list[dict] = []
    descartadas = 0
    vistas: set[str] = set()
    for cuerpo in paginas:
        serie, bajas = parsear_pagina_barras(cuerpo, simbolo)
        descartadas += bajas
        for barra in serie:
            if barra["t"] in vistas:
                continue
            vistas.add(barra["t"])
            velas.append(barra)
    velas.sort(key=lambda b: b["t"])
    dir_sim = exigir_directorio_aislado(almacen) / simbolo
    dir_sim.mkdir(parents=True, exist_ok=True)
    ruta = dir_sim / f"{timeframe}.jsonl"
    temporal = ruta.with_suffix(".jsonl.tmp")
    with temporal.open("w", encoding="utf-8") as fh:
        for barra in velas:
            fh.write(json.dumps(barra, ensure_ascii=False, sort_keys=True) + "\n")
    temporal.replace(ruta)
    meta = {
        "symbol": simbolo,
        "timeframe": timeframe,
        "desde": desde.isoformat(),
        "hasta": hasta.isoformat(),
        "adjustment": AJUSTE,
        "feed": FEED,
        "n_barras": len(velas),
        "descartadas_incompletas": descartadas,
        "truncado": truncado,
        "host": "data.alpaca.markets",
    }
    (dir_sim / f"{timeframe}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return meta


def cargar_almacen(almacen: Path, simbolo: str, timeframe: str) -> list[dict]:
    ruta = almacen / simbolo / f"{timeframe}.jsonl"
    if not ruta.exists():
        return []
    out: list[dict] = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea:
            continue
        try:
            obj = json.loads(linea)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and barra_de_alpaca(obj) is not None:
            out.append(obj)
    return out


def barras_de_chart_yahoo(cuerpo: object) -> tuple[list[dict] | None, str | None]:
    """Velas del chart, o `(None, motivo)` si la forma no es la del chart.

    Un `result` null es un fallo de Yahoo, no una serie vacía. Una vela
    con volumen null se descarta, no se cuenta como volumen cero.
    """
    if not isinstance(cuerpo, dict):
        return None, "cuerpo"
    chart = cuerpo.get("chart")
    if not isinstance(chart, dict) or "result" not in chart:
        return None, "chart_ausente"
    result = chart.get("result")
    if result is None:
        return None, "yahoo_sin_resultado"
    if not isinstance(result, list):
        return None, "chart_ilegible"
    if not result:
        return [], None
    primero = result[0]
    if not isinstance(primero, dict):
        return None, "chart_ilegible"
    timestamps = primero.get("timestamp")
    indicadores = primero.get("indicators")
    if not isinstance(timestamps, list) or not isinstance(indicadores, dict):
        return None, "chart_ilegible"
    quote = indicadores.get("quote")
    if not isinstance(quote, list) or not quote or not isinstance(quote[0], dict):
        return None, "chart_ilegible"
    q = quote[0]
    out: list[dict] = []
    for i, epoch in enumerate(timestamps):
        momento_n = numero(epoch)
        if momento_n is None:
            continue
        try:
            momento = datetime.fromtimestamp(momento_n, tz=UTC)
        except (OSError, OverflowError, ValueError):
            continue
        def _en(campo: str, indice: int = i) -> float | None:
            serie = q.get(campo)
            if not isinstance(serie, list) or indice >= len(serie):
                return None
            return numero(serie[indice])
        o, h, lo, c, v = _en("open"), _en("high"), _en("low"), _en("close"), _en("volume")
        if None in (o, h, lo, c, v):
            continue
        out.append({
            "t": momento.isoformat(timespec="seconds"),
            "o": o, "h": h, "l": lo, "c": c, "v": v,
        })
    return out, None


def pedir_yahoo(
    simbolo: str,
    timeframe: str,
    desde: date,
    hasta: date,
    *,
    pausa: PausaYahoo | None = None,
    transport=None,
    ahora: datetime | None = None,
) -> tuple[list[dict] | None, str | None]:
    """`(velas, motivo)`. Motivo no nulo ⇒ el lado Yahoo no está; las
    velas son None, no `[]`."""
    freno = pausa if pausa is not None else PausaYahoo()
    if freno.activa(ahora):
        return None, "pausa_429"
    if timeframe not in INTERVALO_YAHOO:
        return None, "intervalo"
    inicio = datetime(desde.year, desde.month, desde.day, tzinfo=UTC)
    fin = datetime(hasta.year, hasta.month, hasta.day, tzinfo=UTC) + timedelta(days=1)
    params = {
        "period1": str(int(inicio.timestamp())),
        "period2": str(int(fin.timestamp())),
        "interval": INTERVALO_YAHOO[timeframe],
        "events": "div|split",
    }
    getter = transport or requests.get
    try:
        respuesta = getter(
            CHART_YAHOO.format(t=simbolo),
            params=params,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
    except requests.RequestException as ex:
        log.warning("yahoo chart: %s", type(ex).__name__)
        return None, "red"
    status = getattr(respuesta, "status_code", None)
    if status == 429:
        # No se escribe la pausa del bot: eso apagaría el escaneo. Esta
        # corrida sí se frena, y el motivo queda en el diff.
        log.warning("yahoo chart respondió 429; el diff no trata eso como serie vacía")
        return None, "pausa_429"
    if isinstance(status, int) and status >= 400:
        return None, f"http_{status}"
    try:
        cuerpo = respuesta.json()
    except ValueError:
        return None, "cuerpo"
    return barras_de_chart_yahoo(cuerpo)


def _diff_cierre(sip_c: float | None, yahoo_c: float | None) -> float | None:
    if sip_c is None or yahoo_c is None:
        return None
    if yahoo_c == 0:
        return None
    return (sip_c - yahoo_c) / yahoo_c * 100.0


def mediana(valores: list[float]) -> float | None:
    if not valores:
        return None
    ordenados = sorted(valores)
    n = len(ordenados)
    medio = n // 2
    if n % 2:
        return ordenados[medio]
    return (ordenados[medio - 1] + ordenados[medio]) / 2.0


def diff_barras(sip: list[dict] | None, yahoo: list[dict] | None, timeframe: str) -> dict:
    """Compara por la clave de la vela. Yahoo ausente no se cuenta como
    cero velas ni como diferencia de precio cero."""
    if sip is None:
        return {
            "disponible_sip": False,
            "disponible_yahoo": yahoo is not None,
            "n_sip": None,
            "n_yahoo": None if yahoo is None else len(yahoo),
            "n_en_ambos": None,
            "n_solo_sip": None,
            "n_solo_yahoo": None,
            "n_cierre_no_comparable": None,
            "mediana_diff_cierre_pct": None,
            "mediana_abs_diff_cierre_pct": None,
        }
    por_sip: dict[str, dict] = {}
    for barra in sip:
        clave = clave_de(barra, timeframe)
        if clave is not None:
            por_sip[clave] = barra
    if yahoo is None:
        return {
            "disponible_sip": True,
            "disponible_yahoo": False,
            "n_sip": len(por_sip),
            "n_yahoo": None,
            "n_en_ambos": None,
            "n_solo_sip": None,
            "n_solo_yahoo": None,
            "n_cierre_no_comparable": None,
            "mediana_diff_cierre_pct": None,
            "mediana_abs_diff_cierre_pct": None,
            "motivo_yahoo": "no_disponible",
        }
    por_yahoo: dict[str, dict] = {}
    for barra in yahoo:
        clave = clave_de(barra, timeframe)
        if clave is not None:
            por_yahoo[clave] = barra
    ambos = set(por_sip) & set(por_yahoo)
    diffs: list[float] = []
    no_comparable = 0
    for clave in ambos:
        d = _diff_cierre(numero(por_sip[clave].get("c")), numero(por_yahoo[clave].get("c")))
        if d is None:
            no_comparable += 1
        else:
            diffs.append(d)
    return {
        "disponible_sip": True,
        "disponible_yahoo": True,
        "n_sip": len(por_sip),
        "n_yahoo": len(por_yahoo),
        "n_en_ambos": len(ambos),
        "n_solo_sip": len(set(por_sip) - set(por_yahoo)),
        "n_solo_yahoo": len(set(por_yahoo) - set(por_sip)),
        "n_cierre_no_comparable": no_comparable,
        "mediana_diff_cierre_pct": mediana(diffs),
        "mediana_abs_diff_cierre_pct": mediana([abs(d) for d in diffs]),
    }


def _fmt(n: float | None, sufijo: str = "") -> str:
    if n is None:
        return "sin datos"
    if isinstance(n, float) and not n.is_integer():
        return f"{n:.2f}{sufijo}"
    if isinstance(n, float):
        return f"{int(n)}{sufijo}"
    return f"{n}{sufijo}"


def formatear_diff(simbolo: str, timeframe: str, diff: dict, motivo_yahoo: str | None = None) -> str:
    lineas = [
        f"{simbolo} {timeframe}",
        f"  velas SIP: {_fmt(diff.get('n_sip'))}",
    ]
    if not diff.get("disponible_yahoo"):
        motivo = motivo_yahoo or diff.get("motivo_yahoo") or "no_disponible"
        lineas.append(f"  Yahoo no disponible ({motivo}). No se anota como serie vacía ni como diferencia 0.")
        return "\n".join(lineas)
    lineas.extend([
        f"  velas Yahoo: {_fmt(diff.get('n_yahoo'))}",
        f"  en ambas: {_fmt(diff.get('n_en_ambos'))}",
        f"  solo SIP: {_fmt(diff.get('n_solo_sip'))}",
        f"  solo Yahoo: {_fmt(diff.get('n_solo_yahoo'))}",
        f"  cierres no comparables: {_fmt(diff.get('n_cierre_no_comparable'))}",
        f"  mediana de (SIP − Yahoo) / Yahoo: {_fmt(diff.get('mediana_diff_cierre_pct'), ' %')}",
        f"  mediana del valor absoluto: {_fmt(diff.get('mediana_abs_diff_cierre_pct'), ' %')}",
    ])
    if timeframe == "1Day":
        lineas.append(
            "  La clave diaria es la fecha en America/New_York del timestamp de cada fuente. "
            "Un sello a medianoche UTC puede caer en el día anterior; las velas sin par lo muestran."
        )
    lineas.append(
        "  SIP va ajustado por split (`adjustment=split`), no por dividendo. "
        "El chart de Yahoo que se compara es el `quote`, no el `adjclose`."
    )
    return "\n".join(lineas)


def _parse_fecha(texto: str) -> date:
    try:
        return date.fromisoformat(texto)
    except ValueError:
        raise ErrorDatos("fecha") from None


def _simbolos_de(texto: str) -> list[str]:
    out: list[str] = []
    ya: set[str] = set()
    for parte in texto.split(","):
        simbolo = parte.strip().upper()
        if not simbolo or simbolo in ya:
            continue
        ya.add(simbolo)
        out.append(simbolo)
    return out


def cmd_descargar(args) -> int:
    simbolos = _simbolos_de(args.simbolos)
    if not simbolos:
        log.warning("historia: sin símbolos")
        return 2
    try:
        desde = _parse_fecha(args.desde) if args.desde else DESDE_DEFECTO
        hasta = _parse_fecha(args.hasta) if args.hasta else datetime.now(UTC).date()
    except ErrorDatos:
        log.warning("historia: fecha ilegible")
        return 2
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    for tf in timeframes:
        if tf not in TIMEFRAMES:
            log.warning("historia: intervalo desconocido")
            return 2
    try:
        cliente = ClienteDatos()
        cliente._credenciales()
    except ErrorDatos as ex:
        log.warning("historia: %s", ex.codigo)
        return 2
    try:
        almacen = exigir_directorio_aislado(args.almacen)
    except ValueError:
        log.warning("historia: el almacén cae dentro de un paquete operativo")
        return 2
    fallos = 0
    for simbolo in simbolos:
        for tf in timeframes:
            try:
                meta = descargar_simbolo(cliente, simbolo, tf, desde, hasta, almacen)
            except ErrorDatos as ex:
                log.warning("historia: %s %s falló (%s)", simbolo, tf, ex.codigo)
                fallos += 1
                continue
            log.info(
                "historia: %s %s barras=%s descartadas=%s truncado=%s",
                simbolo, tf, meta["n_barras"], meta["descartadas_incompletas"], meta["truncado"],
            )
    return 2 if fallos else 0


def cmd_diff(args) -> int:
    simbolos = _simbolos_de(args.simbolos)
    if not simbolos:
        log.warning("historia: sin símbolos")
        return 2
    try:
        desde = _parse_fecha(args.desde) if args.desde else DESDE_DEFECTO
        hasta = _parse_fecha(args.hasta) if args.hasta else datetime.now(UTC).date()
    except ErrorDatos:
        log.warning("historia: fecha ilegible")
        return 2
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    pausa = PausaYahoo()
    textos: list[str] = []
    for simbolo in simbolos:
        for tf in timeframes:
            if tf not in TIMEFRAMES:
                log.warning("historia: intervalo desconocido")
                return 2
            sip = cargar_almacen(args.almacen, simbolo, tf)
            meta_path = args.almacen / simbolo / f"{tf}.meta.json"
            if not meta_path.exists() and not sip:
                textos.append(f"{simbolo} {tf}\n  sin barras SIP en el almacén. No hay diff.")
                continue
            yahoo, motivo = pedir_yahoo(simbolo, tf, desde, hasta, pausa=pausa)
            diff = diff_barras(sip, yahoo, tf)
            textos.append(formatear_diff(simbolo, tf, diff, motivo))
    informe = "\n\n".join(textos) + "\n"
    if args.salida:
        exigir_directorio_aislado(args.salida.parent if args.salida.suffix else args.salida)
        args.salida.write_text(informe, encoding="utf-8")
    print(informe, end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Sombra B3: barras SIP split-adjusted a un almacén local, y diff contra Yahoo. Sin conexión al camino operativo.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    comun = argparse.ArgumentParser(add_help=False)
    comun.add_argument("--simbolos", required=True, help="tickers separados por comas")
    comun.add_argument("--desde", default=None, help="YYYY-MM-DD; por omisión 2016-01-01")
    comun.add_argument("--hasta", default=None, help="YYYY-MM-DD inclusive; por omisión hoy UTC")
    comun.add_argument("--timeframes", default="1Day,1Min", help="1Day y/o 1Min")
    comun.add_argument("--almacen", type=Path, required=True, help="directorio local, fuera del hunter")
    p_desc = sub.add_parser("descargar", parents=[comun], help="baja barras SIP al almacén")
    p_diff = sub.add_parser("diff", parents=[comun], help="compara el almacén contra Yahoo")
    p_diff.add_argument("--salida", type=Path, default=None, help="además de stdout, escribe el texto aquí")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.cmd == "descargar":
        return cmd_descargar(args)
    return cmd_diff(args)


if __name__ == "__main__":
    raise SystemExit(main())
