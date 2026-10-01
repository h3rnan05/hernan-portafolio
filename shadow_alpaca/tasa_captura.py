"""Tasa de captura diaria: de las acciones que DE VERDAD se movieron hoy,
¿cuántas vio el bot?

POR QUÉ. El embudo mide lo que entra al bot; nada medía lo que se le
escapa. Sin esa cifra no se puede saber si un filtro está matando las
oportunidades buenas o si simplemente no las hubo.

QUÉ HACE, después del cierre:
  1. Arma la lista de "movers reales" con el screener de Yahoo (el mismo
     que usa `momentum_hunter/movers.py`): precio de $0,75 a $20, +20 %
     o más contra el cierre previo, y volumen del día >= 5 veces el
     promedio de los 20 días anteriores (barras diarias de Yahoo).
  2. Para cada uno anota si lo detectó el flujo actual (entró hoy a la
     watchlist), si lo detectó movers_sombra (clase A o B en
     `movers.jsonl`), si tenía noticia en Yahoo y en Alpaca/Benzinga, y,
     si el flujo actual no lo vio, en qué filtro se cayó.
  3. Escribe un CSV por día, un JSON con el resumen y una fila en el
     histórico. Con `--telegram` manda el resumen.

LO QUE NO HACE, a propósito:
  - No decide nada. No toca filtros, umbrales, la watchlist, el paper
    trader ni órdenes. Las noticias se consultan solo para anotarlas.
  - No inventa. Un dato que falta queda "sin_dato" y no cuenta en
    ningún sentido: un mover sin promedio de 20 días no entra a la
    lista (ni como mover ni como no-mover), una fuente de noticias que
    falló es "error", no "no".

"EN QUÉ FILTRO SE CAYÓ". El escaneo no guarda una traza por acción de
la etapa 1 (precio/liquidez/tamaño). Se usa, en este orden:
  - `intradia:<paso>`: la auditoría de etapa 2 lo tiene (traza real).
  - `noticias:<resultado>:<motivo>`: `noticias_leidas.json` lo tiene
    (traza real, pero ese archivo guarda solo las últimas corridas).
  - `fuera_del_universo`: no está en la lista de símbolos del escaneo.
  - `ranura_no_escaneada`: está en el universo pero ninguna corrida de
    hoy miró su ventana (reconstruido desde el `slot` de la telemetría).
  - `universo:<motivo>` / `tipo_excluido` / `tamano_small_cap` /
    `adr_liquidez`: RECONSTRUIDO aplicando las mismas funciones del
    escaneo a las barras hasta ayer (el escaneo ve el cierre de ayer
    porque la vela de hoy llega en formación y se recorta). La
    metadata es la de ahora, no la de la mañana.
  - `sin_traza_tras_universo`: pasaba los filtros de universo
    reconstruidos y no dejó traza: casi siempre murió en el
    catalizador en una corrida que `noticias_leidas.json` ya no guarda.
Cada etiqueta dice si es traza o reconstrucción; no se maquilla.

Este módulo vive en la sombra: el hunter y el ejecutor no lo importan
(prueba de aislamiento). Solo pide al host de DATOS de Alpaca.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
from collections import Counter
from dataclasses import asdict, dataclass, fields
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from shadow_alpaca.jsonl_log import exigir_directorio_aislado
from shadow_alpaca.lectura import ventana_por_slot
from shadow_alpaca.numeros import numero

log = logging.getLogger("shadow_alpaca.tasa_captura")

NY = ZoneInfo("America/New_York")

# Definición de "mover real" (pedido 2026-10-01). Es la vara para medir
# al bot, no un filtro del bot: cambiarla no cambia nada de lo que opera.
PRECIO_MIN = 0.75
PRECIO_MAX = 20.0
SUBIDA_MIN_PCT = 20.0
RVOL_MIN = 5.0
# Tope de filas de una llamada al screener de Yahoo. Si vuelve lleno, la
# lista puede estar cortada y el resumen lo dice.
TOP_SCREENER = 250

ENV_DIR = "MOMENTUM_TASA_CAPTURA_DIR"
ENV_LIMITE_ESCANEO = "MOMENTUM_SCAN_LIMIT"
LIMITE_ESCANEO_DEFAULT = 1000   # el mismo default que run_scan_paper.sh

SIN_DATO = "sin_dato"
NOTICIA_SI = "si"
NOTICIA_NO = "no"
NOTICIA_SIN_FECHA = "sin_fecha"


# ───────────────────────── movers reales ─────────────────────────

@dataclass(frozen=True)
class FilaScreener:
    ticker: str
    nombre: str | None
    precio: float
    cambio_pct: float
    volumen_dia: float
    hora_dato: datetime


@dataclass
class MoverReal:
    ticker: str
    nombre: str | None
    precio: float
    cambio_pct: float
    volumen_dia: float
    promedio_20d: float
    rvol: float


def construir_query():
    """Se importa acá para que el módulo cargue aunque yfinance falte: la
    corrida entonces falla cerrada con motivo."""
    from yfinance import EquityQuery as Q
    return Q("and", [
        Q("eq", ["region", "us"]),
        Q("gte", ["intradayprice", PRECIO_MIN]),
        Q("lte", ["intradayprice", PRECIO_MAX]),
        Q("gte", ["percentchange", SUBIDA_MIN_PCT]),
    ])


def parsear_screener(respuesta: object, descartes: Counter) -> list[FilaScreener]:
    """Filas con los cinco datos que hacen falta. Una fila a la que le
    falte uno se cuenta en `descartes` y no entra: no se rellena."""
    filas = respuesta.get("quotes") if isinstance(respuesta, dict) else None
    if not isinstance(filas, list):
        raise ValueError("screener_sin_quotes")
    out: list[FilaScreener] = []
    for fila in filas:
        if not isinstance(fila, dict):
            descartes["fila_ilegible"] += 1
            continue
        ticker = fila.get("symbol")
        if not isinstance(ticker, str) or not ticker.strip():
            descartes["sin_symbol"] += 1
            continue
        precio = numero(fila.get("regularMarketPrice"))
        cambio = numero(fila.get("regularMarketChangePercent"))
        volumen = numero(fila.get("regularMarketVolume"))
        epoch = numero(fila.get("regularMarketTime"))
        if precio is None or cambio is None or volumen is None:
            descartes["cotizacion_incompleta"] += 1
            continue
        if epoch is None or epoch <= 0:
            descartes["sin_hora_cotizacion"] += 1
            continue
        nombre = fila.get("shortName") or fila.get("longName")
        out.append(FilaScreener(
            ticker=ticker.strip().upper(), nombre=nombre if isinstance(nombre, str) else None,
            precio=precio, cambio_pct=cambio, volumen_dia=volumen,
            hora_dato=datetime.fromtimestamp(epoch, tz=UTC),
        ))
    return out


def consultar_screener(screen=None, descartes: Counter | None = None) -> tuple[list[FilaScreener], bool]:
    """`(filas, posible_truncado)`. Un fallo se propaga: sin screener no
    hay lista, y el caller lo reporta en vez de decir "cero movers"."""
    descartes = descartes if descartes is not None else Counter()
    if screen is None:
        import yfinance as yf
        screen = yf.screen
    respuesta = screen(construir_query(), size=TOP_SCREENER, sortField="percentchange", sortAsc=False)
    crudas = respuesta.get("quotes") if isinstance(respuesta, dict) else None
    truncado = isinstance(crudas, list) and len(crudas) >= TOP_SCREENER
    return parsear_screener(respuesta, descartes), truncado


def barras_hasta_ayer(b, hoy: str):
    """La misma serie, sin la vela de `hoy` ni posteriores. Es lo que el
    escaneo ve durante la sesión (la vela en formación se recorta)."""
    from momentum_hunter.models import Barras
    if b is None or not getattr(b, "fechas", None):
        return None
    n = sum(1 for f in b.fechas if isinstance(f, str) and f[:10] < hoy)
    if n == 0:
        return None
    return Barras(ticker=b.ticker, fechas=b.fechas[:n], open=b.open[:n], close=b.close[:n],
                  high=b.high[:n], low=b.low[:n], volume=b.volume[:n])


def promedio_20d(b_ayer) -> float | None:
    """Promedio de los últimos 20 días completos. Con menos de 20, o con
    un volumen ausente, no hay promedio: no se promedia lo que hay."""
    if b_ayer is None or len(b_ayer.volume) < 20:
        return None
    ultimos = [numero(v) for v in b_ayer.volume[-20:]]
    if any(v is None for v in ultimos):
        return None
    return sum(ultimos) / 20.0


def clasificar_movers(
    filas: list[FilaScreener], diarias: dict, hoy: str, descartes: Counter,
) -> tuple[list[MoverReal], list[dict]]:
    """`(movers_reales, no_verificables)`. Un no verificable subió +20 %
    en rango de precio pero no se puede saber su volumen relativo: no es
    mover ni no-mover, y se informa aparte."""
    reales: list[MoverReal] = []
    dudosos: list[dict] = []
    for f in filas:
        if f.hora_dato.astimezone(NY).date().isoformat() != hoy:
            # Feriado o símbolo sin operar hoy: el % es de otro día.
            descartes["cotizacion_de_otro_dia"] += 1
            continue
        if not (PRECIO_MIN <= f.precio <= PRECIO_MAX) or f.cambio_pct < SUBIDA_MIN_PCT:
            descartes["fuera_de_criterio"] += 1
            continue
        prom = promedio_20d(barras_hasta_ayer(diarias.get(f.ticker), hoy))
        if prom is None or prom <= 0:
            dudosos.append({"ticker": f.ticker, "cambio_pct": f.cambio_pct, "motivo": "sin_promedio_20d"})
            continue
        rvol = f.volumen_dia / prom
        if rvol < RVOL_MIN:
            descartes["volumen_relativo_bajo"] += 1
            continue
        reales.append(MoverReal(f.ticker, f.nombre, f.precio, f.cambio_pct, f.volumen_dia, prom, rvol))
    reales.sort(key=lambda m: m.cambio_pct, reverse=True)
    return reales, dudosos


# ───────────────────────── lo que dejó el bot ─────────────────────────

def _fecha_ny_de_ts(ts: object) -> str | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        momento = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=UTC)
    return momento.astimezone(NY).date().isoformat()


def detectados_watchlist(entradas: list, hoy: str) -> dict[str, str]:
    """ticker → estado, para las entradas que ENTRARON hoy a la watchlist.
    Una entrada de otro día que sigue ahí no es una detección de hoy."""
    out: dict[str, str] = {}
    for e in entradas:
        ticker = getattr(e, "ticker", None)
        if isinstance(ticker, str) and _fecha_ny_de_ts(getattr(e, "creado_en", None)) == hoy:
            out[ticker.strip().upper()] = str(getattr(e, "estado", "") or SIN_DATO)
    return out


def _lineas_jsonl(paths: list[Path]):
    for path in paths:
        try:
            texto = path.read_text(encoding="utf-8")
        except OSError as ex:
            log.warning("no se pudo leer %s (%s)", path.name, type(ex).__name__)
            continue
        for linea in texto.splitlines():
            linea = linea.strip()
            if not linea:
                continue
            try:
                obj = json.loads(linea)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj


def resultado_sombra(dir_telemetria: Path, dia: str) -> tuple[dict[str, str] | None, str | None]:
    """ticker → "A" / "B" / último motivo de rechazo, sobre todas las
    corridas de movers_sombra de hoy. Una clase en CUALQUIER corrida
    cuenta como detección. Sin archivo: None (sombra apagada), no "nadie".
    """
    paths = sorted(Path(dir_telemetria).glob(f"{dia}/*/movers.jsonl"))
    if not paths:
        return None, "sin_movers_jsonl"
    clase: dict[str, str] = {}
    motivo: dict[str, str] = {}
    corridas = 0
    for obj in _lineas_jsonl(paths):
        candidatas = obj.get("candidatas")
        if not isinstance(candidatas, list):
            continue
        corridas += 1
        for c in candidatas:
            if not isinstance(c, dict) or not isinstance(c.get("ticker"), str):
                continue
            t = c["ticker"].strip().upper()
            if c.get("clase") in ("A", "B"):
                # A gana sobre B: tenía catalizador en alguna corrida.
                if clase.get(t) != "A":
                    clase[t] = c["clase"]
            elif isinstance(c.get("motivo_rechazo"), str):
                motivo[t] = c["motivo_rechazo"]
    if corridas == 0:
        return None, "movers_jsonl_sin_corridas"
    return {**motivo, **clase}, None


def trazas_auditoria(dir_auditoria: Path, dia: str) -> dict[str, str]:
    """ticker → `intradia:<paso>` de la última vez que llegó a etapa 2."""
    path = Path(dir_auditoria) / f"{dia}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as ex:
        log.warning("auditoría ilegible (%s)", type(ex).__name__)
        return {}
    out: dict[str, str] = {}
    corridas = data.get("corridas") if isinstance(data, dict) else None
    for corrida in corridas if isinstance(corridas, list) else []:
        candidatos = corrida.get("candidatos") if isinstance(corrida, dict) else None
        for c in candidatos if isinstance(candidatos, list) else []:
            if not isinstance(c, dict) or not isinstance(c.get("ticker"), str):
                continue
            ev = c.get("evaluacion") if isinstance(c.get("evaluacion"), dict) else {}
            paso = ev.get("paso_detenido") or c.get("decision") or SIN_DATO
            out[c["ticker"].strip().upper()] = f"intradia:{paso}"
    return out


def trazas_noticias_leidas(ruta, dia: str) -> dict[str, str]:
    """ticker → `noticias:<resultado>[:<motivo>]` de las corridas de hoy
    que `noticias_leidas.json` todavía guarda."""
    try:
        with open(os.fspath(ruta), encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as ex:
        log.warning("noticias_leidas ilegible (%s)", type(ex).__name__)
        return {}
    corridas = data.get("corridas") if isinstance(data, dict) else None
    out: dict[str, str] = {}
    # El archivo guarda la más nueva primero; se recorre al revés para
    # que la última corrida quede encima.
    for corrida in reversed(corridas if isinstance(corridas, list) else []):
        if not isinstance(corrida, dict) or _fecha_ny_de_ts(corrida.get("corrida_ts")) != dia:
            continue
        acciones = corrida.get("acciones")
        for a in acciones if isinstance(acciones, list) else []:
            if not isinstance(a, dict) or not isinstance(a.get("ticker"), str):
                continue
            etiqueta = f"noticias:{a.get('resultado') or SIN_DATO}"
            if a.get("motivo"):
                etiqueta += f":{a['motivo']}"
            out[a["ticker"].strip().upper()] = etiqueta
    return out


def tickers_escaneados(
    dir_telemetria: Path, dia: str, simbolos: list[str] | None, limite: int,
) -> tuple[set[str] | None, str | None]:
    """Unión de las ventanas que miraron las corridas de escaneo de hoy.

    Se exige que `n_slots` de la telemetría coincida con el que daría el
    universo de ahora y el límite configurado. Si no coincide (el
    universo cambió o el límite es otro), no se adivina: None.
    """
    if simbolos is None:
        return None, "universo_no_disponible"
    paths = sorted(Path(dir_telemetria).glob(f"{dia}/*/events.jsonl"))
    escaneos = [o for o in _lineas_jsonl(paths) if o.get("modo") == "escaneo"]
    if not escaneos:
        return None, "sin_escaneos_hoy"
    vistos: set[str] = set()
    for ev in escaneos:
        slot, n_slots = ev.get("slot"), ev.get("n_slots")
        if slot is None and n_slots is None:
            # Sin rotación: la corrida miró el universo entero.
            vistos |= set(simbolos)
            continue
        if (isinstance(slot, bool) or not isinstance(slot, int)
                or isinstance(n_slots, bool) or not isinstance(n_slots, int)):
            return None, "slot_ilegible"
        if limite <= 0 or math.ceil(len(simbolos) / limite) != n_slots:
            return None, "universo_o_limite_distinto_al_del_escaneo"
        ventana = ventana_por_slot(simbolos, limite, slot)
        if ventana is None:
            return None, "slot_no_cabe_en_el_universo"
        vistos |= set(ventana)
    return vistos, None


def motivo_universo_reconstruido(b_ayer, meta, cfg) -> str | None:
    """Las mismas ramas de la etapa 1 del escaneo, con las funciones del
    escaneo (no una copia de los umbrales). None = pasaba. Devuelve
    `sin_dato` si no hay barras o metadata con qué decidir."""
    from momentum_hunter.run import (
        _clasificar_banda_de_universo,
        _excede_techo_de_tamano,
        _volumen_promedio,
    )
    if b_ayer is None:
        return f"universo:{SIN_DATO}"
    banda, motivo = _clasificar_banda_de_universo(b_ayer, cfg)
    if banda is None:
        return f"universo:{motivo or SIN_DATO}"
    if meta is None:
        return f"metadata:{SIN_DATO}"
    if meta.es_etf or (cfg.excluir_spac and meta.es_spac) or (cfg.excluir_cef and meta.es_cef):
        return "tipo_excluido"
    if banda != "large" and _excede_techo_de_tamano(meta, b_ayer, cfg):
        return "tamano_small_cap"
    vol = _volumen_promedio(b_ayer)
    if meta.es_adr and (vol is None or vol < cfg.liquidez_minima_adr):
        return "adr_liquidez"
    return None


def motivo_caida(
    ticker: str, *, auditoria: dict[str, str], noticias_leidas: dict[str, str],
    universo: set[str] | None, escaneados: set[str] | None, reconstruido: str | None,
) -> str:
    """Primero las trazas reales, después lo reconstruido (ver docstring
    del módulo). Nunca devuelve vacío: si no se sabe, lo dice."""
    if ticker in auditoria:
        return auditoria[ticker]
    if ticker in noticias_leidas:
        return noticias_leidas[ticker]
    if universo is not None and ticker not in universo:
        return "fuera_del_universo"
    if escaneados is not None and ticker not in escaneados:
        return "ranura_no_escaneada"
    if reconstruido is not None:
        return reconstruido
    if universo is None or escaneados is None:
        return SIN_DATO
    return "sin_traza_tras_universo"


# ───────────────────────── noticias (solo se anotan) ─────────────────────────

def desde_noticias(hoy: date) -> date:
    """Día hábil anterior: cubre el after-hours de ayer y el premarket de
    hoy, que es donde sale casi toda noticia que mueve una small cap."""
    d = hoy - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def fecha_ny(fecha: object) -> date | None:
    """La fecha (en Nueva York) de un sello. Solo-fecha se toma tal cual;
    con hora y huso se convierte. Ilegible: None (no cuenta)."""
    if not isinstance(fecha, str) or len(fecha) < 10:
        return None
    try:
        if len(fecha) == 10:
            return date.fromisoformat(fecha)
        momento = datetime.fromisoformat(fecha.replace("Z", "+00:00"))
    except ValueError:
        return None
    if momento.tzinfo is None:
        return momento.date()
    return momento.astimezone(NY).date()


def resultado_noticia(titulares: list | None, ok: bool, motivo: str | None,
                      desde: date, hoy: date) -> tuple[str, int | None]:
    """`(si|no|sin_fecha|error:<motivo>, titulares en la ventana)`.

    `sin_fecha`: hubo titulares, ninguno fechado en la ventana y alguno
    sin fecha legible. No es un "no": puede que el de hoy sea ese."""
    if not ok or titulares is None:
        return f"error:{motivo or SIN_DATO}", None
    en_ventana = 0
    sin_fecha = 0
    for t in titulares:
        f = fecha_ny(getattr(t, "fecha", None))
        if f is None:
            sin_fecha += 1
        elif desde <= f <= hoy:
            en_ventana += 1
    if en_ventana:
        return NOTICIA_SI, en_ventana
    if sin_fecha:
        return NOTICIA_SIN_FECHA, 0
    return NOTICIA_NO, 0


# ───────────────────────── tabla y resumen ─────────────────────────

@dataclass
class FilaCaptura:
    fecha: str
    ticker: str
    nombre: str | None
    precio: float
    cambio_pct: float
    volumen_dia: float
    promedio_20d: float
    rvol: float
    detectado_watchlist: str        # si | no | sin_dato
    estado_watchlist: str | None
    detectado_sombra: str           # si | no | sin_dato
    detalle_sombra: str | None      # A | B | motivo de rechazo | no_salio_en_screener
    noticia_yahoo: str
    titulares_yahoo: int | None
    noticia_alpaca: str
    titulares_alpaca: int | None
    motivo_no_detectado: str | None


COLUMNAS = [f.name for f in fields(FilaCaptura)]


def construir_filas(
    movers: list[MoverReal], hoy: str, *, watchlist: dict[str, str] | None,
    sombra: dict[str, str] | None, noticias_yahoo: dict[str, tuple[str, int | None]],
    noticias_alpaca: dict[str, tuple[str, int | None]], motivos: dict[str, str],
) -> list[FilaCaptura]:
    """Núcleo puro: todo inyectado."""
    filas = []
    for m in movers:
        if watchlist is None:
            det_wl, estado = SIN_DATO, None
        else:
            det_wl, estado = ("si", watchlist[m.ticker]) if m.ticker in watchlist else ("no", None)
        if sombra is None:
            det_s, det_s_detalle = SIN_DATO, None
        elif sombra.get(m.ticker) in ("A", "B"):
            det_s, det_s_detalle = "si", sombra[m.ticker]
        else:
            det_s, det_s_detalle = "no", sombra.get(m.ticker, "no_salio_en_screener")
        ny, ny_n = noticias_yahoo.get(m.ticker, (SIN_DATO, None))
        na, na_n = noticias_alpaca.get(m.ticker, (SIN_DATO, None))
        filas.append(FilaCaptura(
            fecha=hoy, ticker=m.ticker, nombre=m.nombre, precio=round(m.precio, 4),
            cambio_pct=round(m.cambio_pct, 2), volumen_dia=m.volumen_dia,
            promedio_20d=round(m.promedio_20d, 1), rvol=round(m.rvol, 2),
            detectado_watchlist=det_wl, estado_watchlist=estado,
            detectado_sombra=det_s, detalle_sombra=det_s_detalle,
            noticia_yahoo=ny, titulares_yahoo=ny_n, noticia_alpaca=na, titulares_alpaca=na_n,
            motivo_no_detectado=None if det_wl == "si" else motivos.get(m.ticker, SIN_DATO),
        ))
    return filas


def _conteo(filas: list[FilaCaptura], campo: str) -> dict:
    """`{detectados, con_dato, pct}`. Las filas sin dato no entran al
    denominador: un flujo apagado no es un flujo que no vio nada."""
    con_dato = [f for f in filas if getattr(f, campo) != SIN_DATO]
    si = sum(1 for f in con_dato if getattr(f, campo) == "si")
    return {"detectados": si, "con_dato": len(con_dato),
            "pct": round(100.0 * si / len(con_dato), 1) if con_dato else None}


def _conteo_noticia(filas: list[FilaCaptura], campo: str) -> dict:
    valores = Counter(getattr(f, campo) for f in filas)
    errores = sum(v for k, v in valores.items() if k.startswith("error") or k == SIN_DATO)
    return {"si": valores.get(NOTICIA_SI, 0), "no": valores.get(NOTICIA_NO, 0),
            "sin_fecha": valores.get(NOTICIA_SIN_FECHA, 0), "error": errores}


def resumir(filas: list[FilaCaptura], hoy: str, *, no_verificables: list[dict],
            descartes: Counter, truncado: bool, error: str | None = None,
            avisos: list[str] | None = None) -> dict:
    ambos = sum(1 for f in filas if f.detectado_watchlist == "si" and f.detectado_sombra == "si")
    ninguno = sum(1 for f in filas if f.detectado_watchlist == "no" and f.detectado_sombra == "no")
    motivos = Counter(f.motivo_no_detectado for f in filas if f.motivo_no_detectado)
    return {
        "fecha": hoy,
        "error": error,
        "criterio": {"precio_min": PRECIO_MIN, "precio_max": PRECIO_MAX,
                     "subida_min_pct": SUBIDA_MIN_PCT, "rvol_min": RVOL_MIN},
        "movers_reales": len(filas),
        "watchlist": _conteo(filas, "detectado_watchlist"),
        "sombra": _conteo(filas, "detectado_sombra"),
        "ambos": ambos,
        "ninguno": ninguno,
        "noticia_yahoo": _conteo_noticia(filas, "noticia_yahoo"),
        "noticia_alpaca": _conteo_noticia(filas, "noticia_alpaca"),
        "motivos_no_detectado": dict(motivos.most_common()),
        "no_verificables": no_verificables,
        "descartes_screener": dict(descartes),
        "screener_posiblemente_truncado": truncado,
        "avisos": avisos or [],
    }


def _pct(c: dict) -> str:
    if c["pct"] is None:
        return "sin dato"
    return f"{c['detectados']}/{c['con_dato']} ({c['pct']:.0f} %)"


def formatear(resumen: dict, filas: list[FilaCaptura], max_filas: int = 15) -> str:
    """Texto plano para Telegram."""
    cab = f"Tasa de captura {resumen['fecha']}"
    if resumen.get("error"):
        return f"{cab}\nNo se pudo armar la lista de movers reales ({resumen['error']}). Hoy no hay cifra."
    c = resumen["criterio"]
    lineas = [cab, f"Movers reales (${c['precio_min']}-${c['precio_max']:.0f}, +{c['subida_min_pct']:.0f} %, "
                    f"volumen ≥{c['rvol_min']:.0f}x su promedio de 20 días): {resumen['movers_reales']}"]
    if resumen["movers_reales"] == 0:
        lineas.append("Hoy ninguna acción cumplió el criterio.")
    else:
        ny, na = resumen["noticia_yahoo"], resumen["noticia_alpaca"]
        lineas += [
            f"Detectó el flujo actual (watchlist): {_pct(resumen['watchlist'])}",
            f"Detectó movers_sombra: {_pct(resumen['sombra'])}",
            f"Los dos: {resumen['ambos']} · ninguno: {resumen['ninguno']}",
            f"Con noticia en Yahoo: {ny['si']} (sin fecha {ny['sin_fecha']}, error {ny['error']})",
            f"Con noticia en Alpaca/Benzinga: {na['si']} (sin fecha {na['sin_fecha']}, error {na['error']})",
        ]
        if resumen["motivos_no_detectado"]:
            top = ", ".join(f"{k} {v}" for k, v in list(resumen["motivos_no_detectado"].items())[:5])
            lineas.append(f"Dónde se cayeron (flujo actual): {top}")
        lineas.append("")
        for f in filas[:max_filas]:
            wl = f"WL {f.detectado_watchlist}" + (f" ({f.motivo_no_detectado})" if f.motivo_no_detectado else "")
            sombra = f"sombra {f.detectado_sombra}" + (f" ({f.detalle_sombra})" if f.detalle_sombra else "")
            lineas.append(f"{f.ticker} {f.cambio_pct:+.0f} % · {f.rvol:.0f}x · {wl} · {sombra} · "
                          f"Y:{f.noticia_yahoo} A:{f.noticia_alpaca}")
        if len(filas) > max_filas:
            lineas.append(f"… y {len(filas) - max_filas} más en el CSV.")
    if resumen["no_verificables"]:
        lineas.append(f"Subieron +{c['subida_min_pct']:.0f} % sin promedio de 20 días (no se cuentan): "
                      + ", ".join(d["ticker"] for d in resumen["no_verificables"][:10]))
    if resumen["screener_posiblemente_truncado"]:
        lineas.append(f"Aviso: el screener devolvió el tope de {TOP_SCREENER} filas; la lista puede estar cortada.")
    for aviso in resumen.get("avisos") or []:
        lineas.append(f"Aviso: {aviso}")
    return "\n".join(lineas)


# ───────────────────────── disco ─────────────────────────

def dir_salida(explicito: Path | None = None) -> Path:
    if explicito is not None:
        return explicito
    crudo = os.environ.get(ENV_DIR, "").strip()
    if crudo:
        return Path(crudo)
    from momentum_hunter.rutas_estado import raiz
    return raiz() / "tasa_captura"


HISTORICO = "historico.csv"
COLUMNAS_HISTORICO = ["fecha", "movers_reales", "watchlist_detectados", "watchlist_pct",
                      "sombra_detectados", "sombra_pct", "ambos", "ninguno",
                      "noticia_yahoo_si", "noticia_alpaca_si", "error"]


def _fila_historico(r: dict) -> dict:
    return {
        "fecha": r["fecha"], "movers_reales": r.get("movers_reales"),
        "watchlist_detectados": (r.get("watchlist") or {}).get("detectados"),
        "watchlist_pct": (r.get("watchlist") or {}).get("pct"),
        "sombra_detectados": (r.get("sombra") or {}).get("detectados"),
        "sombra_pct": (r.get("sombra") or {}).get("pct"),
        "ambos": r.get("ambos"), "ninguno": r.get("ninguno"),
        "noticia_yahoo_si": (r.get("noticia_yahoo") or {}).get("si"),
        "noticia_alpaca_si": (r.get("noticia_alpaca") or {}).get("si"),
        "error": r.get("error"),
    }


def _escribir_atomico(path: Path, texto: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(texto, encoding="utf-8")
    os.replace(tmp, path)


def _csv(columnas: list[str], filas: list[dict]) -> str:
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columnas, lineterminator="\n")
    w.writeheader()
    for f in filas:
        # Un None es una celda vacía, no un 0.
        w.writerow({k: ("" if f.get(k) is None else f.get(k)) for k in columnas})
    return buf.getvalue()


def guardar(carpeta: Path, filas: list[FilaCaptura], resumen: dict) -> Path:
    """CSV del día + JSON del resumen + una fila por día en el histórico.
    Volver a correr el mismo día REEMPLAZA esa fecha, no la duplica."""
    exigir_directorio_aislado(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    hoy = resumen["fecha"]
    _escribir_atomico(carpeta / f"{hoy}.csv", _csv(COLUMNAS, [asdict(f) for f in filas]))
    _escribir_atomico(carpeta / f"{hoy}.resumen.json",
                      json.dumps(resumen, ensure_ascii=False, indent=2, default=str) + "\n")
    hist = carpeta / HISTORICO
    previas: list[dict] = []
    if hist.exists():
        try:
            with hist.open(encoding="utf-8", newline="") as fh:
                previas = [r for r in csv.DictReader(fh) if r.get("fecha") != hoy]
        except (OSError, csv.Error) as ex:
            log.warning("histórico ilegible (%s): se reescribe desde hoy", type(ex).__name__)
            previas = []
    previas.append(_fila_historico(resumen))
    previas.sort(key=lambda r: r.get("fecha") or "")
    _escribir_atomico(hist, _csv(COLUMNAS_HISTORICO, previas))
    return carpeta


# ───────────────────────── la corrida ─────────────────────────

def _noticias_yahoo(tickers: list[str], desde: date, hoy: date, ahora: datetime,
                    proveedor=None, pausa=None) -> dict[str, tuple[str, int | None]]:
    from momentum_hunter.catalysts.detector import YahooNewsProvider
    from momentum_hunter.data.provider import PausaYahoo
    from shadow_alpaca.noticias import _yahoo_de
    proveedor = proveedor if proveedor is not None else YahooNewsProvider()
    pausa = pausa if pausa is not None else PausaYahoo()
    pausa_activa = pausa.activa(ahora)
    limite = False
    out: dict[str, tuple[str, int | None]] = {}
    for t in tickers:
        titulares, ok, motivo, seguir = _yahoo_de(t, proveedor, pausa_activa, limite)
        if not seguir:
            limite = True
        out[t] = resultado_noticia(titulares, ok, motivo, desde, hoy)
    return out


def _noticias_alpaca(tickers: list[str], desde: date, hoy: date, ahora: datetime,
                     cliente=None) -> dict[str, tuple[str, int | None]]:
    from shadow_alpaca.cliente import ClienteDatos, ErrorDatos
    from shadow_alpaca.noticias import pedir_alpaca
    if not tickers:
        return {}
    cliente = cliente if cliente is not None else ClienteDatos()
    inicio = datetime.combine(desde, time(0, 0), tzinfo=NY)
    try:
        por_ticker, fallidos, motivos, _meta = pedir_alpaca(cliente, tickers, inicio, ahora)
    except ErrorDatos as ex:
        return {t: (f"error:{ex.codigo}", None) for t in tickers}
    out = {}
    for t in tickers:
        if t in fallidos:
            out[t] = (f"error:{motivos.get(t, SIN_DATO)}", None)
        else:
            out[t] = resultado_noticia(por_ticker.get(t, []), True, None, desde, hoy)
    return out


def correr(
    *, ahora: datetime | None = None, screen=None, provider=None, entradas_watchlist=None,
    dir_telemetria: Path | None = None, dir_auditoria: Path | None = None,
    ruta_noticias_leidas=None, simbolos_universo=None, limite_escaneo: int | None = None,
    proveedor_yahoo=None, pausa_yahoo=None, cliente_alpaca=None, cfg=None,
) -> tuple[list[FilaCaptura], dict]:
    """Toda la corrida, con cada fuente inyectable para las pruebas.
    Nunca escribe nada del bot; lo que falla queda como aviso."""
    from momentum_hunter.config import CONFIG
    cfg = cfg or CONFIG
    ahora = ahora or datetime.now(UTC)
    hoy_d = ahora.astimezone(NY).date()
    hoy = hoy_d.isoformat()
    avisos: list[str] = []
    descartes: Counter = Counter()

    try:
        crudas, truncado = consultar_screener(screen, descartes)
    except Exception as ex:  # fail-closed: sin lista no hay cifra
        log.warning("tasa de captura: el screener falló (%s)", type(ex).__name__)
        return [], resumir([], hoy, no_verificables=[], descartes=descartes, truncado=False,
                           error=f"screener:{type(ex).__name__}")

    if provider is None and crudas:
        from momentum_hunter.data.fuente import proveedor_configurado
        from momentum_hunter.data.provider import YahooProvider
        provider = proveedor_configurado(construir_yahoo=YahooProvider)
    candidatos = sorted({f.ticker for f in crudas})
    diarias: dict = {}
    if candidatos:
        try:
            # 280 días como el escaneo: `_clasificar_banda_de_universo`
            # mira la misma historia que miró él.
            diarias = provider.barras(candidatos, dias=280)
        except Exception as ex:
            avisos.append(f"barras diarias: {type(ex).__name__}")
    movers, dudosos = clasificar_movers(crudas, diarias, hoy, descartes)
    if crudas and descartes.get("cotizacion_de_otro_dia", 0) == len(crudas):
        avisos.append("todas las cotizaciones son de otro día (¿feriado o mercado cerrado?)")
    tickers = [m.ticker for m in movers]

    # Flujo actual.
    if entradas_watchlist is None:
        try:
            from momentum_hunter import watchlist
            entradas_watchlist = (watchlist.cargar(apply_vps_state=True) if watchlist.vps_state_habilitado()
                                  else watchlist.cargar())
        except Exception as ex:
            avisos.append(f"watchlist: {type(ex).__name__}")
    wl = detectados_watchlist(entradas_watchlist, hoy) if entradas_watchlist is not None else None

    from momentum_hunter.audit import DIR_AUDITORIA
    from momentum_hunter.noticias_leidas import ARCHIVO as NOTICIAS_LEIDAS
    from momentum_hunter.telemetria import DIR_TELEMETRIA
    dir_telemetria = Path(os.fspath(dir_telemetria if dir_telemetria is not None else DIR_TELEMETRIA))
    dir_auditoria = Path(os.fspath(dir_auditoria if dir_auditoria is not None else DIR_AUDITORIA))
    ruta_noticias_leidas = ruta_noticias_leidas if ruta_noticias_leidas is not None else NOTICIAS_LEIDAS

    sombra, motivo_sombra = resultado_sombra(dir_telemetria, hoy)
    if sombra is None:
        avisos.append(f"movers_sombra sin datos hoy ({motivo_sombra})")

    # La auditoría se nombra por fecha UTC; la sesión de NY entera cae
    # en el mismo día UTC, pero por si acaso se leen las dos.
    auditoria = {**trazas_auditoria(dir_auditoria, ahora.astimezone(UTC).date().isoformat()),
                 **trazas_auditoria(dir_auditoria, hoy)}
    leidas = trazas_noticias_leidas(ruta_noticias_leidas, hoy)

    no_detectados = [t for t in tickers if wl is not None and t not in wl]
    universo = None
    if simbolos_universo is None and no_detectados:
        try:
            from momentum_hunter.universe import tickers as tickers_universo
            simbolos_universo = tickers_universo()
        except Exception as ex:
            avisos.append(f"universo: {type(ex).__name__}")
    if simbolos_universo is not None:
        universo = set(simbolos_universo)
    if limite_escaneo is None:
        crudo = os.environ.get(ENV_LIMITE_ESCANEO, "").strip()
        limite_escaneo = int(crudo) if crudo.isdigit() else LIMITE_ESCANEO_DEFAULT
    escaneados, motivo_esc = (tickers_escaneados(dir_telemetria, hoy, simbolos_universo, limite_escaneo)
                              if no_detectados else (None, None))
    if no_detectados and escaneados is None:
        avisos.append(f"ranuras escaneadas: no se pudieron reconstruir ({motivo_esc})")

    # Reconstrucción de etapa 1 solo para los que no tienen traza real.
    sin_traza = [t for t in no_detectados if t not in auditoria and t not in leidas
                 and (universo is None or t in universo)
                 and (escaneados is None or t in escaneados)]
    metadata: dict = {}
    if sin_traza:
        try:
            metadata = provider.metadata(sin_traza)
        except Exception as ex:
            avisos.append(f"metadata: {type(ex).__name__}")
    motivos: dict[str, str] = {}
    for t in no_detectados:
        reconstruido = None
        if t in sin_traza:
            try:
                reconstruido = motivo_universo_reconstruido(
                    barras_hasta_ayer(diarias.get(t), hoy), metadata.get(t), cfg)
            except Exception as ex:
                reconstruido = f"reconstruccion_fallo:{type(ex).__name__}"
        motivos[t] = motivo_caida(t, auditoria=auditoria, noticias_leidas=leidas, universo=universo,
                                  escaneados=escaneados, reconstruido=reconstruido)

    desde = desde_noticias(hoy_d)
    noticias_y = _noticias_yahoo(tickers, desde, hoy_d, ahora, proveedor_yahoo, pausa_yahoo)
    noticias_a = _noticias_alpaca(tickers, desde, hoy_d, ahora, cliente_alpaca)

    filas = construir_filas(movers, hoy, watchlist=wl, sombra=sombra, noticias_yahoo=noticias_y,
                            noticias_alpaca=noticias_a, motivos=motivos)
    resumen = resumir(filas, hoy, no_verificables=dudosos, descartes=descartes,
                      truncado=truncado, avisos=avisos)
    return filas, resumen


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Tasa de captura diaria (solo lectura).")
    ap.add_argument("--telegram", action="store_true", help="además del CSV, manda el resumen por Telegram")
    ap.add_argument("--salida", type=Path, default=None, help="carpeta de salida (default: estado/tasa_captura)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    filas, resumen = correr()
    texto = formatear(resumen, filas)
    print(texto)
    try:
        carpeta = guardar(dir_salida(args.salida), filas, resumen)
        log.info("tasa de captura escrita en %s", carpeta)
    except (OSError, ValueError) as ex:
        log.warning("tasa de captura: no se pudo escribir (%s)", type(ex).__name__)
    if args.telegram:
        from momentum_hunter.run import enviar_telegram
        enviar_telegram(texto)
    return 2 if resumen.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
