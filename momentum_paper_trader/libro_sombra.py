"""Libro sombra -- qué habría pasado, con precios reales, si el sistema
hubiera operado con filtros distintos. Nunca envía órdenes.

POR QUÉ EXISTE (2026-09-23, fase 0 de la estrategia aprobada por el
dueño). El sistema lleva UNA operación cerrada y cada umbral está
elegido por razonamiento, no por evidencia. Cambiar varios a la vez y
mirar el P&L real tarda meses y no dice cuál cambio funcionó. Este
módulo prueba VARIANTES en paralelo, con riesgo cero: al cierre de cada
día toma las señales que el sistema vio (disparadas, "casi" disparadas,
movers sin catalizador, y un control aleatorio), baja las velas de 1
minuto de ese día, y simula cada entrada con su stop y su objetivo,
cobrando deslizamiento. En semanas hay muestra por variante, no en
meses.

VARIANTES (cada una es un selector de señales + parámetros):
  disparadas        todas las TRIGGERED del día, sin la compuerta de IA
  ia_confianza_5    TRIGGERED cuya revisión tuvo confianza >= 5 (cota
                    superior: `revisiones.json` guarda el veredicto ya
                    re-validado, ver `embudo.py`)
  ia_real           lo que el sistema DE VERDAD colocó (control)
  score_45/score_50 ticker-días con patrón + timing + riesgo pero score
                    por debajo del mínimo: entran en la primera lectura
                    que cruza 45 / 50
  sin_catalizador   movers de clase B (dinero entrando, sin noticia)
  corte_1130        `disparadas` solo hasta las 11:30 ET
  stop_05atr        `disparadas` con piso de stop en 0,5 × ATR (2R igual)
  time_stop_30      `disparadas` cerrando a los 30 min si no va +0,5R
  aleatorio         entradas al azar en los movers del día, mismo stop
                    que el sistema: si el sistema no le gana a esto, el
                    edge está en el universo, no en la entrada

SIMULACIÓN (mismas reglas que el ejecutor real, en la medida en que las
velas de 1 minuto permiten saberlo): compra limitada al precio de
entrada, llenada en la primera vela cuyo mínimo lo toca dentro de
`minutos_max_llenado` (si no, "no_llenada", como la cancelación real a
15 min); deslizamiento en el llenado y en el stop, nunca en el objetivo
(es una orden limitada); si en la misma vela se tocan stop y objetivo se
asume el stop (conservador); sin salida a `minutos_antes_del_cierre`
del cierre regular, se cierra al precio de esa vela (como `cierre.py`).
MFE/MAE en R por trade, para saber si el stop está mal puesto.

QUÉ NO ES. No es un backtest de la estrategia entera (las velas del
universo completo no existen); mide solo las señales que el sistema
llegó a ver. Los ATR de las señales sin entrada en la watchlist se
aproximan con los días previos de la misma serie de 1 minuto y se
marcan como aproximados. Un dato que falta deja la señal fuera, nunca
se inventa (regla 6). Vive en el paper trader porque cruza las dos
fases; el hunter no lo importa.

USO
  python -m momentum_paper_trader.libro_sombra --dia 2026-09-22       # simula y guarda
  python -m momentum_paper_trader.libro_sombra --resumen --desde ... --hasta ...
"""

from __future__ import annotations

import argparse
import json
import logging
import random
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from momentum_hunter import report, telemetria as telem_hunter, watchlist
from momentum_hunter.audit import DIR_AUDITORIA
from momentum_hunter.config import CONFIG as CFG_HUNTER
from momentum_hunter.data.provider import DataProvider
from momentum_hunter.models import BarraIntradia, FactoresIntradia
from momentum_paper_trader import estado
from momentum_paper_trader import telemetria as telem_paper

log = logging.getLogger("momentum_paper_trader.libro_sombra")

ARCHIVO = "sombra.json"
ARCHIVO_MOVERS = "movers.jsonl"
_NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class Parametros:
    slippage_pct: float = 0.004          # 0,4 % en llenado y en stop (rango 0,3-0,5 % pedido)
    minutos_max_llenado: float = 15.0    # como `minutos_maximos_entrada_sin_llenar`
    minutos_antes_del_cierre: int = 10   # como `cierre.py`
    time_stop_minutos: float | None = None
    time_stop_r: float = 0.5
    aleatorias_por_dia: int = 5


@dataclass(frozen=True)
class Senal:
    variante: str
    ticker: str
    t0: str                      # ISO UTC: desde cuándo se puede llenar
    entrada: float
    stop: float
    objetivo: float
    origen: str                  # triggered | casi | clase_b | aleatoria | ia_real
    es_large_cap: bool | None = None
    atr_aproximado: bool = False


@dataclass
class Resultado:
    variante: str
    ticker: str
    origen: str
    t0: str
    entrada: float
    stop: float
    objetivo: float
    es_large_cap: bool | None
    atr_aproximado: bool
    llenada: bool
    precio_llenado: float | None = None
    llenado_ts: str | None = None
    salida_ts: str | None = None
    precio_salida: float | None = None
    motivo_salida: str | None = None    # stop | objetivo | cierre | time_stop | no_llenada | fin_de_datos
    r: float | None = None
    mfe_r: float | None = None
    mae_r: float | None = None
    minutos: float | None = None


# --------------------------------------------------------------------------
# Velas
# --------------------------------------------------------------------------

def _ts(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(UTC)


def velas_del_dia(bi: BarraIntradia, dia: str) -> BarraIntradia:
    idx = [i for i, t in enumerate(bi.timestamps) if t[:10] == dia]
    return BarraIntradia(
        bi.ticker, [bi.timestamps[i] for i in idx], [bi.open[i] for i in idx],
        [bi.close[i] for i in idx], [bi.high[i] for i in idx],
        [bi.low[i] for i in idx], [bi.volume[i] for i in idx])


def atr_aproximado(bi: BarraIntradia, dia: str) -> float | None:
    """Rango (máximo - mínimo) medio de los días ANTERIORES a `dia` en la
    misma serie de 1 minuto. No es el ATR diario de la etapa 1 (ese usa
    barras diarias y el cierre previo); es lo que se puede saber sin
    pedir más datos, y se marca como aproximado en cada señal."""
    por_dia: dict[str, list[float]] = {}
    for t, h, lo in zip(bi.timestamps, bi.high, bi.low):
        d = t[:10]
        if d < dia and h is not None and lo is not None:
            por_dia.setdefault(d, []).extend([h, lo])
    rangos = [max(v) - min(v) for v in por_dia.values() if v]
    if not rangos:
        return None
    return sum(rangos) / len(rangos)


def _hora_cierre_utc(dia: str, minutos_antes: int) -> datetime:
    ny = datetime.fromisoformat(dia).replace(hour=16, minute=0, tzinfo=_NY)
    return (ny - timedelta(minutes=minutos_antes)).astimezone(UTC)


def _antes_de(dia: str, hora_et: tuple[int, int]) -> datetime:
    return datetime.fromisoformat(dia).replace(hour=hora_et[0], minute=hora_et[1], tzinfo=_NY).astimezone(UTC)


# --------------------------------------------------------------------------
# Simulación de UNA señal
# --------------------------------------------------------------------------

def simular(s: Senal, velas: BarraIntradia, p: Parametros, dia: str) -> Resultado:
    r = Resultado(variante=s.variante, ticker=s.ticker, origen=s.origen, t0=s.t0,
                  entrada=s.entrada, stop=s.stop, objetivo=s.objetivo,
                  es_large_cap=s.es_large_cap, atr_aproximado=s.atr_aproximado, llenada=False)
    riesgo = s.entrada - s.stop
    if riesgo <= 0 or not velas.timestamps:
        r.motivo_salida = "no_llenada"
        return r
    t0 = _ts(s.t0)
    limite_llenado = t0 + timedelta(minutes=p.minutos_max_llenado)
    cierre = _hora_cierre_utc(dia, p.minutos_antes_del_cierre)

    i_llenado = None
    for i, t in enumerate(velas.timestamps):
        ts = _ts(t)
        if ts < t0:
            continue
        if ts > limite_llenado or ts >= cierre:
            break
        if velas.low[i] is not None and velas.low[i] <= s.entrada:
            i_llenado = i
            break
    if i_llenado is None:
        r.motivo_salida = "no_llenada"
        return r

    llenado = s.entrada * (1 + p.slippage_pct)
    r.llenada, r.precio_llenado, r.llenado_ts = True, round(llenado, 4), velas.timestamps[i_llenado]
    t_llenado = _ts(velas.timestamps[i_llenado])
    time_stop_en = t_llenado + timedelta(minutes=p.time_stop_minutos) if p.time_stop_minutos else None
    mfe = mae = 0.0

    def _cerrar(i: int, precio: float, motivo: str) -> None:
        r.salida_ts, r.precio_salida, r.motivo_salida = velas.timestamps[i], round(precio, 4), motivo
        r.r = round((precio - llenado) / riesgo, 3)
        r.mfe_r, r.mae_r = round(mfe / riesgo, 3), round(mae / riesgo, 3)
        r.minutos = round((_ts(velas.timestamps[i]) - t_llenado).total_seconds() / 60.0, 1)

    for i in range(i_llenado + 1, len(velas.timestamps)):
        ts = _ts(velas.timestamps[i])
        hi, lo, cl = velas.high[i], velas.low[i], velas.close[i]
        if hi is None or lo is None or cl is None:
            continue
        mfe = max(mfe, hi - llenado)
        mae = max(mae, llenado - lo)
        if lo <= s.stop:
            _cerrar(i, s.stop * (1 - p.slippage_pct), "stop")
            return r
        if hi >= s.objetivo:
            _cerrar(i, s.objetivo, "objetivo")
            return r
        if time_stop_en is not None and ts >= time_stop_en and cl < llenado + p.time_stop_r * riesgo:
            _cerrar(i, cl * (1 - p.slippage_pct), "time_stop")
            return r
        if ts >= cierre:
            _cerrar(i, cl * (1 - p.slippage_pct), "cierre")
            return r
    ultimo = len(velas.timestamps) - 1
    _cerrar(ultimo, velas.close[ultimo] * (1 - p.slippage_pct), "fin_de_datos")
    return r


# --------------------------------------------------------------------------
# Selectores de señales
# --------------------------------------------------------------------------

def _niveles(precio, vwap, ema9, atr) -> tuple[float, float, float] | None:
    n = report.niveles_entrada_salida(FactoresIntradia(precio_actual=precio, vwap=vwap, ema9=ema9), atr)
    if n["entrada"] is None or n["stop"] is None or n["objetivo"] is None:
        return None
    return float(n["entrada"]), float(n["stop"]), float(n["objetivo"])


def _con_piso(entrada: float, stop: float, atr: float | None, fraccion: float) -> tuple[float, float]:
    if atr is None or atr <= 0:
        return stop, entrada + (entrada - stop) * 2.0
    stop2 = min(stop, entrada - atr * fraccion)
    return stop2, entrada + (entrada - stop2) * 2.0


def senales_disparadas(entradas: list, dia: str) -> list[Senal]:
    out = []
    for e in entradas:
        if str(e.creado_en or "")[:10] != dia:
            continue
        disparo = next((t for t in (e.transiciones or []) if t.estado == watchlist.ESTADO_TRIGGERED), None)
        if disparo is None or e.ultima_entrada is None or e.ultimo_stop is None or e.ultimo_objetivo is None:
            continue
        out.append(Senal("disparadas", e.ticker, str(disparo.timestamp), float(e.ultima_entrada),
                         float(e.ultimo_stop), float(e.ultimo_objetivo), "triggered", e.es_large_cap))
    return out


def senales_ia(disparadas: list[Senal], revisiones: list, dia: str, umbral: int) -> list[Senal]:
    por_ticker = {s.ticker: s for s in disparadas}
    out = []
    for r in revisiones:
        if str(r.timestamp or "")[:10] != dia or r.ia_entraria is None or int(r.confianza) < umbral:
            continue
        s = por_ticker.get(r.ticker)
        if s is None:
            continue
        out.append(Senal(f"ia_confianza_{umbral}", s.ticker, str(r.timestamp), s.entrada, s.stop, s.objetivo,
                         "triggered", s.es_large_cap))
    return out


def senales_ia_real(revisiones: list, dia: str) -> list[Senal]:
    out = []
    for r in revisiones:
        if str(r.timestamp or "")[:10] != dia or not r.entro:
            continue
        if r.precio_entrada is None or r.stop is None or r.objetivo is None:
            continue
        out.append(Senal("ia_real", r.ticker, str(r.timestamp), float(r.precio_entrada), float(r.stop),
                         float(r.objetivo), "ia_real", r.es_large_cap))
    return out


def _cargar_auditoria_dia(dia: str, dir_auditoria: Path) -> list[dict]:
    path = dir_auditoria / f"{dia}.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text()).get("corridas", [])
    except (json.JSONDecodeError, OSError):
        log.warning("auditoría ilegible, se omite: %s", path.name)
        return []


def senales_casi(corridas: list[dict], dia: str, umbral: float, atr_por_ticker: dict[str, float | None],
                 large_por_ticker: dict[str, bool | None], umbral_real: float) -> list[Senal]:
    """Ticker-días que nunca fueron accionables pero tuvieron patrón +
    temprano + riesgo con score >= `umbral` en alguna lectura: entran en
    la PRIMERA de esas lecturas, con niveles recalculados desde ese
    snapshot (misma función que el pipeline)."""
    accionables: set[str] = set()
    primera: dict[str, tuple[str, dict]] = {}
    for c in corridas:
        ts = str(c.get("timestamp", ""))
        for cand in c.get("candidatos", []):
            ev = cand.get("evaluacion") if isinstance(cand.get("evaluacion"), dict) else None
            if ev is None:
                continue
            t = str(cand.get("ticker", "?"))
            if ev.get("accionable"):
                accionables.add(t)
            sc = ev.get("score_ajustado")
            if (ev.get("patron") and ev.get("temprano") and ev.get("riesgo_definido") is True
                    and isinstance(sc, (int, float)) and umbral <= sc < umbral_real and t not in primera):
                primera[t] = (ts, cand)
    out = []
    for t, (ts, cand) in primera.items():
        if t in accionables:
            continue
        fi = cand.get("factores_intradia") or {}
        atr = atr_por_ticker.get(t)
        niv = _niveles(fi.get("precio_actual"), fi.get("vwap"), fi.get("ema9"), atr)
        if niv is None:
            continue
        out.append(Senal(f"score_{umbral:.0f}", t, ts, *niv, "casi", large_por_ticker.get(t),
                         atr_aproximado=True))
    return out


def _cargar_movers_dia(dia: str, dir_telemetria: Path) -> list[dict]:
    out = []
    for path in sorted(dir_telemetria.glob(f"{dia}/*/{ARCHIVO_MOVERS}")):
        try:
            for linea in path.read_text(encoding="utf-8").splitlines():
                if linea.strip():
                    c = json.loads(linea)
                    if isinstance(c, dict):
                        out.append(c)
        except (json.JSONDecodeError, OSError):
            log.warning("telemetría de movers ilegible: %s", path)
    return out


def candidatas_movers(corridas: list[dict]) -> list[dict]:
    """Primera lectura de cada ticker en la sombra del día, con su hora."""
    vistas: dict[str, dict] = {}
    for c in corridas:
        ts = str(c.get("timestamp", ""))
        for cand in c.get("candidatas", []) or []:
            if not isinstance(cand, dict) or not cand.get("ticker"):
                continue
            t = cand["ticker"]
            if t not in vistas:
                vistas[t] = {**cand, "_ts": cand.get("hora_dato") or ts}
    return list(vistas.values())


def senales_sin_catalizador(cands: list[dict], atr_por_ticker: dict[str, float | None]) -> list[Senal]:
    out = []
    for cand in cands:
        if cand.get("clase") != "B":
            continue
        niv = _niveles(cand.get("precio_intradia"), cand.get("vwap"), None, atr_por_ticker.get(cand["ticker"]))
        if niv is None or not cand.get("_ts"):
            continue
        out.append(Senal("sin_catalizador", cand["ticker"], str(cand["_ts"]), *niv, "clase_b", None,
                         atr_aproximado=True))
    return out


def senales_aleatorias(cands: list[dict], dia: str, velas_por_ticker: dict[str, BarraIntradia],
                       atr_por_ticker: dict[str, float | None], n: int) -> list[Senal]:
    """Control: `n` tickers del screener de movers elegidos al azar (semilla
    = el día, reproducible), entrada al cierre de una vela al azar entre
    las 10:05 y las 11:30 ET, stop con el mismo piso que el sistema
    (0,25 × ATR), objetivo 2R."""
    tickers = sorted({c["ticker"] for c in cands if c.get("ticker")})
    rng = random.Random(dia)
    rng.shuffle(tickers)
    out = []
    desde, hasta = _antes_de(dia, (10, 5)), _antes_de(dia, (11, 30))
    for t in tickers:
        if len(out) >= n:
            break
        v = velas_por_ticker.get(t)
        atr = atr_por_ticker.get(t)
        if v is None or atr is None or atr <= 0:
            continue
        idx = [i for i, ts in enumerate(v.timestamps) if desde <= _ts(ts) <= hasta and v.close[i] is not None]
        if not idx:
            continue
        i = rng.choice(idx)
        entrada = float(v.close[i])
        stop, objetivo = _con_piso(entrada, entrada, atr, report.FRACCION_ATR_MINIMA_STOP)
        out.append(Senal("aleatorio", t, v.timestamps[i], entrada, stop, objetivo, "aleatoria", None,
                         atr_aproximado=True))
    return out


# --------------------------------------------------------------------------
# Corrida del día
# --------------------------------------------------------------------------

def construir_senales(
    dia: str, entradas: list, revisiones: list, corridas_audit: list[dict], cands_movers: list[dict],
    velas_por_ticker: dict[str, BarraIntradia], p: Parametros, umbral_real: float,
) -> tuple[list[Senal], dict[str, Parametros]]:
    """Todas las señales de todas las variantes, más los parámetros de
    simulación por variante (solo `time_stop_30` cambia los suyos)."""
    atr_por_ticker: dict[str, float | None] = {
        t: atr_aproximado(v, dia) for t, v in velas_por_ticker.items()}
    large: dict[str, bool | None] = {}
    for e in entradas:
        if str(e.creado_en or "")[:10] == dia:
            large[e.ticker] = e.es_large_cap
            if e.atr_diario is not None:
                atr_por_ticker[e.ticker] = float(e.atr_diario)   # el real le gana al aproximado

    disparadas = senales_disparadas(entradas, dia)
    senales: list[Senal] = list(disparadas)
    senales += senales_ia(disparadas, revisiones, dia, 5)
    senales += senales_ia_real(revisiones, dia)
    for u in (45.0, 50.0):
        senales += senales_casi(corridas_audit, dia, u, atr_por_ticker, large, umbral_real)
    senales += senales_sin_catalizador(cands_movers, atr_por_ticker)
    corte = _antes_de(dia, (11, 30))
    senales += [Senal("corte_1130", s.ticker, s.t0, s.entrada, s.stop, s.objetivo, s.origen, s.es_large_cap)
                for s in disparadas if _ts(s.t0) <= corte]
    for s in disparadas:
        atr = atr_por_ticker.get(s.ticker)
        stop2, obj2 = _con_piso(s.entrada, s.stop, atr, 0.5)
        senales.append(Senal("stop_05atr", s.ticker, s.t0, s.entrada, stop2, obj2, s.origen, s.es_large_cap,
                             atr_aproximado=(atr is not None and not any(
                                 e.ticker == s.ticker and e.atr_diario is not None for e in entradas))))
    senales += [Senal("time_stop_30", s.ticker, s.t0, s.entrada, s.stop, s.objetivo, s.origen, s.es_large_cap)
                for s in disparadas]
    senales += senales_aleatorias(cands_movers, dia, velas_por_ticker, atr_por_ticker, p.aleatorias_por_dia)
    params = {"time_stop_30": Parametros(
        slippage_pct=p.slippage_pct, minutos_max_llenado=p.minutos_max_llenado,
        minutos_antes_del_cierre=p.minutos_antes_del_cierre, time_stop_minutos=30.0,
        time_stop_r=p.time_stop_r, aleatorias_por_dia=p.aleatorias_por_dia)}
    return senales, params


def tickers_necesarios(dia: str, entradas: list, revisiones: list, corridas_audit: list[dict],
                       cands_movers: list[dict]) -> list[str]:
    t: set[str] = set()
    t |= {e.ticker for e in entradas if str(e.creado_en or "")[:10] == dia}
    t |= {r.ticker for r in revisiones if str(r.timestamp or "")[:10] == dia}
    for c in corridas_audit:
        t |= {str(x.get("ticker")) for x in c.get("candidatos", []) if x.get("ticker")}
    t |= {c["ticker"] for c in cands_movers if c.get("ticker")}
    return sorted(t)


def correr_dia(
    dia: str, provider: DataProvider, *, p: Parametros = Parametros(),
    path_watchlist: Path = watchlist.PATH, path_revisiones: Path = estado.PATH,
    dir_auditoria: Path = DIR_AUDITORIA, dir_telemetria_hunter: Path = telem_hunter.DIR_TELEMETRIA,
    dir_telemetria_paper: Path = telem_paper.DIR_TELEMETRIA, fuente: str | None = None,
    umbral_real: float = CFG_HUNTER.score_minimo_alerta,
) -> dict:
    """Simula todas las variantes del día y guarda
    `telemetria/<dia>/<fuente>/sombra.json` (el persist ya sube ese
    directorio). Devuelve lo guardado."""
    entradas = watchlist.cargar(path_watchlist) if path_watchlist.exists() else []
    revisiones = estado.cargar(path_revisiones) if path_revisiones.exists() else []
    corridas_audit = _cargar_auditoria_dia(dia, dir_auditoria)
    cands = candidatas_movers(_cargar_movers_dia(dia, dir_telemetria_hunter))

    tickers = tickers_necesarios(dia, entradas, revisiones, corridas_audit, cands)
    velas_por_ticker: dict[str, BarraIntradia] = {}
    if tickers:
        try:
            crudas = provider.barras_intradia(tickers, intervalo="1m", periodo="5d")
        except Exception as ex:
            log.warning("no se pudieron pedir velas (%s); el libro sombra del día queda vacío", type(ex).__name__)
            crudas = {}
        velas_por_ticker = {t: b for t, b in crudas.items() if b is not None and b.timestamps}

    senales, params = construir_senales(dia, entradas, revisiones, corridas_audit, cands,
                                        velas_por_ticker, p, umbral_real)
    resultados: list[Resultado] = []
    sin_velas: list[str] = []
    for s in senales:
        bi = velas_por_ticker.get(s.ticker)
        if bi is None:
            sin_velas.append(f"{s.variante}:{s.ticker}")
            continue
        resultados.append(simular(s, velas_del_dia(bi, dia), params.get(s.variante, p), dia))

    salida = {
        "dia": dia,
        "generado_en": datetime.now(UTC).isoformat(timespec="seconds"),
        "parametros": asdict(p),
        "tickers_pedidos": len(tickers),
        "tickers_con_velas": len(velas_por_ticker),
        "senales_sin_velas": sin_velas,
        "resultados": [asdict(r) for r in resultados],
    }
    ruta = dir_telemetria_paper / dia / telem_paper.resolver_fuente(fuente) / ARCHIVO
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(salida, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("libro sombra %s: %d señales, %d simuladas, guardado en %s", dia, len(senales), len(resultados), ruta)
    return salida


# --------------------------------------------------------------------------
# Resumen por variante
# --------------------------------------------------------------------------

@dataclass
class Metrica:
    variante: str
    senales: int = 0
    llenadas: int = 0
    ganadoras: int = 0
    r_total: float = 0.0
    r_positivas: float = 0.0
    r_negativas: float = 0.0
    mfe: list[float] = field(default_factory=list)
    mae: list[float] = field(default_factory=list)
    ganadoras_con_mae_alto: int = 0     # tocaron -0,8R antes de ganar: el stop estaba dentro del ruido
    drawdown_max_r: float = 0.0
    salidas: dict[str, int] = field(default_factory=dict)

    @property
    def tasa_acierto(self) -> float | None:
        return self.ganadoras / self.llenadas if self.llenadas else None

    @property
    def expectativa_r(self) -> float | None:
        return self.r_total / self.llenadas if self.llenadas else None

    @property
    def profit_factor(self) -> float | None:
        if self.r_negativas == 0:
            return None
        return self.r_positivas / abs(self.r_negativas)


def cargar_resultados(desde: str, hasta: str, dir_telemetria_paper: Path = telem_paper.DIR_TELEMETRIA) -> list[dict]:
    out = []
    if not dir_telemetria_paper.exists():
        return out
    for path in sorted(dir_telemetria_paper.glob(f"*/*/{ARCHIVO}")):
        dia = path.parent.parent.name
        if not (desde <= dia <= hasta):
            continue
        try:
            out.extend(json.loads(path.read_text(encoding="utf-8")).get("resultados", []))
        except (json.JSONDecodeError, OSError):
            log.warning("libro sombra ilegible, se omite: %s", path)
    return out


def resumir(resultados: list[dict]) -> dict[str, Metrica]:
    por_variante: dict[str, Metrica] = {}
    acumulado: dict[str, float] = {}
    pico: dict[str, float] = {}
    for r in sorted(resultados, key=lambda x: (x.get("variante", ""), x.get("t0", ""))):
        v = str(r.get("variante", "?"))
        m = por_variante.setdefault(v, Metrica(v))
        m.senales += 1
        m.salidas[str(r.get("motivo_salida"))] = m.salidas.get(str(r.get("motivo_salida")), 0) + 1
        if not r.get("llenada") or not isinstance(r.get("r"), (int, float)):
            continue
        rr = float(r["r"])
        m.llenadas += 1
        m.r_total += rr
        if rr > 0:
            m.ganadoras += 1
            m.r_positivas += rr
            if isinstance(r.get("mae_r"), (int, float)) and r["mae_r"] >= 0.8:
                m.ganadoras_con_mae_alto += 1
        else:
            m.r_negativas += rr
        if isinstance(r.get("mfe_r"), (int, float)):
            m.mfe.append(float(r["mfe_r"]))
        if isinstance(r.get("mae_r"), (int, float)):
            m.mae.append(float(r["mae_r"]))
        acumulado[v] = acumulado.get(v, 0.0) + rr
        pico[v] = max(pico.get(v, 0.0), acumulado[v])
        m.drawdown_max_r = max(m.drawdown_max_r, pico[v] - acumulado[v])
    return por_variante


def _f(x: float | None, fmt: str = "{:.2f}") -> str:
    return fmt.format(x) if isinstance(x, (int, float)) else "-"


def formatear_resumen(metricas: dict[str, Metrica], desde: str, hasta: str) -> str:
    l = [f"LIBRO SOMBRA {desde} → {hasta}  (R por trade, con deslizamiento; muestra mínima para decidir: 50 por variante)",
         f"{'variante':<16}{'señales':>8}{'llenadas':>9}{'acierto':>9}{'exp. R':>8}{'PF':>7}{'MFE':>7}{'MAE':>7}{'gan. MAE≥0,8R':>15}{'DD máx R':>10}"]
    for v, m in metricas.items():
        mfe = sum(m.mfe) / len(m.mfe) if m.mfe else None
        mae = sum(m.mae) / len(m.mae) if m.mae else None
        l.append(f"{v:<16}{m.senales:>8}{m.llenadas:>9}{_f(m.tasa_acierto, '{:.0%}'):>9}{_f(m.expectativa_r):>8}"
                 f"{_f(m.profit_factor):>7}{_f(mfe):>7}{_f(mae):>7}{m.ganadoras_con_mae_alto:>15}{_f(m.drawdown_max_r):>10}")
        l.append("      salidas: " + ", ".join(f"{k} {n}" for k, n in sorted(m.salidas.items())))
    if not metricas:
        l.append("  (sin resultados en el rango)")
    return "\n".join(l)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Libro sombra: trades hipotéticos por variante, sin órdenes.")
    p.add_argument("--dia", help="simular y guardar este día (ISO)")
    p.add_argument("--resumen", action="store_true", help="resumen por variante del rango")
    hoy = datetime.now(UTC).date()
    p.add_argument("--desde", default=(hoy - timedelta(days=13)).isoformat())
    p.add_argument("--hasta", default=hoy.isoformat())
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if a.dia:
        from momentum_hunter.data.provider import YahooProvider
        salida = correr_dia(a.dia, YahooProvider())
        print(formatear_resumen(resumir(salida["resultados"]), a.dia, a.dia))
    if a.resumen or not a.dia:
        print(formatear_resumen(resumir(cargar_resultados(a.desde, a.hasta)), a.desde, a.hasta))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
