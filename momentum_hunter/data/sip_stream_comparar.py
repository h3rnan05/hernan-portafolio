"""Compara las barras del stream SIP con las del REST de Alpaca.

No decide y no coloca órdenes. En sombra (el default) es la única
lectura que se hace del almacén, además de la del propio proceso que
lo escribe. `primario` tampoco pasa por acá para entrar: el hunter
usa `sip_stream.barras_si_cubren`, y si el almacén no cubre, el REST.

TOLERANCIAS. No son umbrales del bot. Solo dicen si esta línea del
informe marca la vela. El websocket manda la vela cruda; el REST del
hunter pide `adjustment=split`. En un día sin split tienen que
coincidir.

  - Precio (o, h, l, c): si el REST es >= $1, 0,01 de diferencia
    absoluta (un centavo, el tick de Alpaca en ese rango). Si el REST
    es < $1, 0,0001 (cuatro decimales, el tick por debajo del dólar).
  - Volumen: exacto. Es un conteo. No hay holgura.
  - Un campo ausente en un lado no se compara y no se sustituye por
    cero: va a `ausentes`.

LAG. `lag_ms = recibido_en - (t + 60s)`. La vela de minuto sale
cuando el minuto cierra, así que el reloj esperado es el borde
derecho. Positivo: llegó después del cierre. Negativo: el reloj de
esta máquina está adelantado, y no se aplasta a cero. Si falta
`recibido_en`, `lag_ms` queda null.

COBERTURA. `minutos_en_ambos / minutos_rest` cuando el REST se pudo
leer y trajo al menos un minuto. Si el REST falló, el ratio queda
null: no es cero y no es "cubrimos todo". Si los dos lados están
vacíos con un REST leído, el ratio también queda null: no hay
evidencia, no un 100 %.

USO
  python -m momentum_hunter.data.sip_stream_comparar --dia 2026-09-28
  python -m momentum_hunter.data.sip_stream_comparar --dia 2026-09-28 --pedir-rest
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from momentum_hunter.data.sip_stream import (
    _clasificar_archivo,
    _numero,
    directorio_stream,
    fecha_ny,
    minuto_iso,
    parse_instante,
)

log = logging.getLogger("momentum_hunter.data.sip_stream_comparar")

_NY = ZoneInfo("America/New_York")

# Ver el docstring. No los lee el hunter para entrar ni para el stop.
TOLERANCIA_PRECIO_ABS = 0.01
TOLERANCIA_PRECIO_SUBDOLAR = 0.0001
TOLERANCIA_VOLUMEN = 0.0

_CAMPOS_PRECIO = ("o", "h", "l", "c")


@dataclass
class Informe:
    dia: str
    fuentes: dict
    n_barras_stream: int | None
    n_barras_rest: int | None
    discrepancias: list = field(default_factory=list)
    ausentes: list = field(default_factory=list)
    solo_stream: list = field(default_factory=list)
    solo_rest: list = field(default_factory=list)
    lags: list = field(default_factory=list)
    cobertura: dict = field(default_factory=dict)
    huecos: list = field(default_factory=list)
    saltos: list = field(default_factory=list)
    lineas_ilegibles: int = 0


def codigo_salida(inf: Informe) -> int:
    """2 si no se pudo comparar. 1 si hay diferencias o campos ausentes.
    0 si los minutos que están en los dos lados coinciden y no sobra
    ninguno. Un lag null no es un fallo."""
    if inf.fuentes.get("stream") in ("ausente", "ilegible", None):
        return 2
    if inf.fuentes.get("rest") in ("ausente", "ilegible", None):
        return 2
    if inf.discrepancias or inf.ausentes or inf.solo_stream or inf.solo_rest:
        return 1
    return 0


def precio_dentro(stream: float | None, rest: float | None) -> bool | None:
    """True si caen en la tolerancia, False si no, None si falta un
    lado. None no es False y no es un precio cero."""
    if stream is None or rest is None:
        return None
    tol = TOLERANCIA_PRECIO_SUBDOLAR if abs(rest) < 1 else TOLERANCIA_PRECIO_ABS
    return abs(stream - rest) <= tol


def volumen_dentro(stream: float | None, rest: float | None) -> bool | None:
    if stream is None or rest is None:
        return None
    return abs(stream - rest) <= TOLERANCIA_VOLUMEN


def lag_ms(recibido_en: object, minuto: object) -> float | None:
    """Milisegundos entre el cierre del minuto y el momento en que
    este proceso escribió la vela. None si falta un reloj."""
    llegada = parse_instante(recibido_en)
    borde = parse_instante(minuto_iso(minuto) if not isinstance(minuto, datetime) else minuto.isoformat())
    if llegada is None or borde is None:
        return None
    cierre = borde + timedelta(seconds=60)
    return round((llegada - cierre).total_seconds() * 1000.0, 1)


def _clave(simbolo: object, minuto: object) -> tuple[str, str] | None:
    if not isinstance(simbolo, str) or not simbolo.strip():
        return None
    m = minuto_iso(minuto)
    if m is None:
        return None
    return simbolo.strip().upper(), m


def _indexar(filas: list[dict] | None) -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    if not filas:
        return out
    for fila in filas:
        if not isinstance(fila, dict):
            continue
        clave = _clave(fila.get("S") or fila.get("symbol") or fila.get("ticker"), fila.get("t"))
        if clave is None:
            continue
        out[clave] = fila
    return out


def _cobertura(n_stream: int | None, n_rest: int | None, n_ambos: int, rest_ok: bool) -> dict:
    if not rest_ok or n_rest is None or n_stream is None:
        ratio = None
    elif n_rest == 0:
        ratio = None
    else:
        ratio = round(n_ambos / n_rest, 4)
    return {
        "minutos_stream": n_stream,
        "minutos_rest": n_rest,
        "minutos_en_ambos": n_ambos if n_stream is not None and n_rest is not None else None,
        "ratio": ratio,
    }


def comparar(
    dia: str,
    *,
    stream: list[dict] | None,
    rest: list[dict] | None,
    huecos: list[dict] | None = None,
    saltos: list[dict] | None = None,
    lineas_ilegibles: int = 0,
    rest_ilegible: bool = False,
) -> Informe:
    """`stream is None` o `rest is None` es "no se leyó". Una lista
    vacía es "se leyó y no había velas". No se declara cobertura 0
    cuando una fuente falta."""
    if not isinstance(dia, str) or len(dia) != 10:
        raise ValueError("dia")
    fuentes = {
        "stream": "ausente" if stream is None else ("vacia" if stream == [] else "presente"),
        "rest": "ilegible" if rest_ilegible else (
            "ausente" if rest is None else ("vacia" if rest == [] else "presente")
        ),
    }
    if stream is None or rest is None or rest_ilegible:
        return Informe(
            dia=dia,
            fuentes=fuentes,
            n_barras_stream=None if stream is None else len(stream),
            n_barras_rest=None if rest is None or rest_ilegible else len(rest),
            huecos=list(huecos or []),
            saltos=list(saltos or []),
            lineas_ilegibles=lineas_ilegibles,
            cobertura=_cobertura(
                None if stream is None else len(_indexar(stream)),
                None,
                0,
                False,
            ),
        )

    idx_s = _indexar(stream)
    idx_r = _indexar(rest)
    discrepancias: list[dict] = []
    ausentes: list[dict] = []
    lags: list[dict] = []
    ambos = set(idx_s) & set(idx_r)
    for clave in sorted(ambos):
        simbolo, minuto = clave
        fs, fr = idx_s[clave], idx_r[clave]
        for campo in _CAMPOS_PRECIO:
            vs, vr = _numero(fs.get(campo)), _numero(fr.get(campo))
            veredicto = precio_dentro(vs, vr)
            if veredicto is None:
                ausentes.append({
                    "S": simbolo, "t": minuto, "campo": campo, "stream": vs, "rest": vr,
                })
            elif not veredicto:
                discrepancias.append({
                    "S": simbolo, "t": minuto, "campo": campo, "stream": vs, "rest": vr,
                })
        vs, vr = _numero(fs.get("v")), _numero(fr.get("v"))
        veredicto_v = volumen_dentro(vs, vr)
        if veredicto_v is None:
            ausentes.append({"S": simbolo, "t": minuto, "campo": "v", "stream": vs, "rest": vr})
        elif not veredicto_v:
            discrepancias.append({
                "S": simbolo, "t": minuto, "campo": "v", "stream": vs, "rest": vr,
            })
        lags.append({
            "S": simbolo,
            "t": minuto,
            "recibido_en": fs.get("recibido_en"),
            "lag_ms": lag_ms(fs.get("recibido_en"), minuto),
        })

    solo_stream = [
        {"S": s, "t": t} for (s, t) in sorted(set(idx_s) - set(idx_r))
    ]
    solo_rest = [
        {"S": s, "t": t} for (s, t) in sorted(set(idx_r) - set(idx_s))
    ]
    return Informe(
        dia=dia,
        fuentes=fuentes,
        n_barras_stream=len(idx_s),
        n_barras_rest=len(idx_r),
        discrepancias=discrepancias,
        ausentes=ausentes,
        solo_stream=solo_stream,
        solo_rest=solo_rest,
        lags=lags,
        cobertura=_cobertura(len(idx_s), len(idx_r), len(ambos), True),
        huecos=list(huecos or []),
        saltos=list(saltos or []),
        lineas_ilegibles=lineas_ilegibles,
    )


def _barras_del_jsonl(directorio: Path, dia: str) -> tuple[str, list[dict] | None, int]:
    path = directorio / "barras" / f"{dia}.jsonl"
    if not path.is_file():
        return "ausente", None, 0
    try:
        texto = path.read_text(encoding="utf-8")
    except OSError:
        return "ilegible", None, 0
    out: list[dict] = []
    malas = 0
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
        dia_barra = fecha_ny(obj.get("t"))
        if dia_barra is None:
            # Sin reloj no es una vela de este día ni un cero.
            malas += 1
            continue
        if dia_barra != dia:
            continue
        out.append(obj)
    return "presente", out, malas


def _eventos(directorio: Path, dia: str, tipo: str) -> list[dict]:
    path = directorio / "eventos" / f"{dia}.jsonl"
    if not path.is_file():
        return []
    try:
        texto = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for linea in texto.splitlines():
        if not linea.strip():
            continue
        try:
            obj = json.loads(linea)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("tipo") == tipo:
            out.append(obj)
    return out


def barras_rest_a_filas(barras: dict) -> list[dict]:
    """`BarraIntradia` o listas paralelas → filas {S, t, o, h, l, c, v}.

    Un volumen None en la serie no entra: no se escribe como 0."""
    filas = []
    for ticker, serie in (barras or {}).items():
        timestamps = getattr(serie, "timestamps", None)
        if timestamps is None and isinstance(serie, dict):
            timestamps = serie.get("t") or serie.get("timestamps")
            opens = serie.get("o") or serie.get("open")
            highs = serie.get("h") or serie.get("high")
            lows = serie.get("l") or serie.get("low")
            closes = serie.get("c") or serie.get("close")
            vols = serie.get("v") or serie.get("volume")
        else:
            opens = getattr(serie, "open", None)
            highs = getattr(serie, "high", None)
            lows = getattr(serie, "low", None)
            closes = getattr(serie, "close", None)
            vols = getattr(serie, "volume", None)
        if not isinstance(timestamps, list):
            continue
        for i, ts in enumerate(timestamps):
            try:
                o, h, lo, c, v = opens[i], highs[i], lows[i], closes[i], vols[i]
            except (IndexError, TypeError):
                continue
            if None in (_numero(o), _numero(h), _numero(lo), _numero(c), _numero(v)):
                continue
            minuto = minuto_iso(ts)
            if minuto is None or fecha_ny(minuto) != fecha_ny(ts):
                # fecha_ny(minuto) usa el mismo instante. Si el día NY
                # no es el pedido, lo filtra el caller.
                pass
            filas.append({
                "S": str(ticker).upper(),
                "t": minuto,
                "o": _numero(o),
                "h": _numero(h),
                "l": _numero(lo),
                "c": _numero(c),
                "v": _numero(v),
            })
    return filas


def pedir_rest(simbolos: list[str], dia: str):
    """REST SIP de ese día de sesión, o (`ilegible`, None) si el ciclo
    falla. No convierte el fallo en lista vacía."""
    inicio = datetime.strptime(dia, "%Y-%m-%d").replace(tzinfo=_NY)
    fin = inicio + timedelta(days=1)
    try:
        from momentum_hunter.data.alpaca_datos import AlpacaProvider
        # Compara stream SIP contra REST SIP: IEX no sirve de referencia.
        prov = AlpacaProvider(feed="sip", respaldo_iex=False)
        params = {
            "timeframe": "1Min",
            "start": inicio.astimezone(UTC).isoformat(timespec="seconds"),
            "end": fin.astimezone(UTC).isoformat(timespec="seconds"),
            "limit": 10_000,
            "adjustment": "split",
            "feed": "sip",
            "sort": "asc",
        }
        crudas = prov._velas_de_lotes(simbolos, params, 15, intradia=True)
    except Exception as ex:
        log.warning("REST SIP no se pudo leer (%s)", type(ex).__name__)
        return "ilegible", None
    filas = []
    for ticker, listas in crudas.items():
        marcas, o, h, lo, c, vol = listas
        for i, ts in enumerate(marcas):
            if fecha_ny(ts) != dia:
                continue
            filas.append({
                "S": str(ticker).upper(),
                "t": minuto_iso(ts),
                "o": o[i], "h": h[i], "l": lo[i], "c": c[i], "v": vol[i],
            })
    return "presente", filas


def comparar_dia(
    dia: str,
    directorio: Path,
    *,
    rest: list[dict] | None = None,
    rest_ilegible: bool = False,
    simbolos: list[str] | None = None,
    pedir: bool = False,
) -> Informe:
    est_s, stream, malas = _barras_del_jsonl(directorio, dia)
    if est_s == "ilegible":
        stream = None
    if pedir:
        if not simbolos:
            estado_path = directorio / "estado.json"
            est, data = _clasificar_archivo(estado_path)
            if est == "presente" and isinstance(data, dict) and isinstance(data.get("suscritos"), list):
                simbolos = [s for s in data["suscritos"] if isinstance(s, str)]
            else:
                simbolos = sorted({b["S"] for b in (stream or []) if isinstance(b.get("S"), str)})
        est_r, rest = pedir_rest(simbolos or [], dia)
        rest_ilegible = est_r != "presente"
        if rest_ilegible:
            rest = None
    inf = comparar(
        dia,
        stream=stream,
        rest=rest,
        huecos=_eventos(directorio, dia, "hueco"),
        saltos=_eventos(directorio, dia, "salto"),
        lineas_ilegibles=malas,
        rest_ilegible=rest_ilegible,
    )
    if est_s == "ilegible":
        inf.fuentes["stream"] = "ilegible"
        inf.n_barras_stream = None
    return inf


def comparar_almacen(directorio: Path, ahora: datetime | None = None) -> Informe | None:
    """Una pasada de telemetría del día NY en curso. La escribe al
    JSONL del almacén. Si no hay barras ni REST, igual deja constancia
    de que se intentó: un archivo que no existe no es cero diferencias,
    y el informe lo dice con fuentes ausentes."""
    reloj = ahora or datetime.now(UTC)
    if reloj.tzinfo is None:
        reloj = reloj.replace(tzinfo=UTC)
    dia = reloj.astimezone(_NY).date().isoformat()
    try:
        inf = comparar_dia(dia, directorio, pedir=True)
    except (OSError, ValueError) as ex:
        log.warning("comparador SIP falló (%s)", type(ex).__name__)
        return None
    registro = asdict(inf)
    registro["tipo"] = "comparacion"
    registro["recibido_en"] = reloj.astimezone(UTC).isoformat(timespec="milliseconds")
    path = directorio / "telemetria" / f"{dia}.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError as ex:
        log.warning("no se pudo escribir la telemetría SIP (%s)", type(ex).__name__)
    log.info(
        "comparacion SIP dia=%s stream=%s rest=%s discrepancias=%d solo_rest=%d ratio=%s",
        dia,
        inf.fuentes.get("stream"),
        inf.fuentes.get("rest"),
        len(inf.discrepancias),
        len(inf.solo_rest),
        inf.cobertura.get("ratio"),
    )
    return inf


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Compara barras del stream SIP con el REST. No coloca órdenes.",
    )
    ap.add_argument("--dia", required=True, help="sesión America/New_York, YYYY-MM-DD")
    ap.add_argument("--directorio", type=Path, default=None)
    ap.add_argument("--rest", type=Path, default=None, help="JSON de filas {S,t,o,h,l,c,v}")
    ap.add_argument("--pedir-rest", action="store_true")
    ap.add_argument("--salida", type=Path, default=None)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        datetime.strptime(args.dia, "%Y-%m-%d")
    except ValueError:
        log.error("dia ilegible; no se compara")
        return 2
    if args.pedir_rest and args.rest is not None:
        log.error("pida el REST o pase un archivo, no las dos cosas")
        return 2
    directorio = args.directorio or directorio_stream()
    rest = None
    ilegible = False
    if args.rest is not None:
        est, data = _clasificar_archivo(args.rest)
        if est != "presente" or not isinstance(data, list):
            ilegible = True
        else:
            rest = data
    elif not args.pedir_rest:
        rest = None
    if args.pedir_rest:
        inf = comparar_dia(args.dia, directorio, pedir=True)
    else:
        inf = comparar_dia(args.dia, directorio, rest=rest, rest_ilegible=ilegible)
    texto = json.dumps(asdict(inf), ensure_ascii=False, indent=2)
    if args.salida is not None:
        args.salida.parent.mkdir(parents=True, exist_ok=True)
        args.salida.write_text(texto + "\n", encoding="utf-8")
    else:
        print(texto)
    return codigo_salida(inf)


if __name__ == "__main__":
    raise SystemExit(main())
