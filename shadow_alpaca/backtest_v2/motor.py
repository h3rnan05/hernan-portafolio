"""Motor del backtest v2: embudo de candidatos, señal minuto a minuto y
simulación de cartera con las reglas de riesgo v2.

EMBUDO por sesión (cada etapa cuenta cuántos pasan, para el informe):
  1. universo      NYSE/NASDAQ/AMEX del listado, sin ETF (marca del listado).
  2. pre-gap       apertura de la vela diaria vs cierre previo >= la MITAD
                   del umbral. Solo para no pedir subastas de todo el
                   mercado; la decisión es la del paso 3.
  3. gap oficial   subasta de apertura vs subasta de cierre previa (#202).
  4. acciones      sin acción corporativa con fecha efectiva ese día (#201).
  5. noticias      al menos una noticia Benzinga en las 24 h previas a las
                   ventana_fin del día.
  6. técnica       `estrategia_v2.reglas.evaluar_ruptura` en cada vela de la
                   ventana (gap, rango de apertura, volumen, VWAP, RVOL, SPY,
                   precio) y sin veto en las noticias vigentes.
  7. metadata      float en [min, max] y no ETF/SPAC (Yahoo ACTUAL). Sin
                   metadata o sin float: excluido (nunca cero). Es un filtro
                   de universo por día: mirarlo después de la técnica da el
                   mismo resultado y pide a Yahoo solo lo necesario.
  8. catalizador   la IA clasifica (titular + resumen); hace falta un nivel
                   operable, alcista, publicado antes del minuto de la señal
                   y dentro de las 24 h.
  9. spread        quote SIP del minuto; el primer cruce que pasa TODO es LA
                   señal del día (una entrada por símbolo y día).

EJECUCIÓN SIMULADA (reglas de `config/estrategia_v2.yaml`):
  - Orden a la apertura de la vela siguiente a la señal (el sistema sin IA
    tarda ~15 s desde el cierre de la vela; ver #205) + slippage por lado.
  - Stop = mínimo del rango de apertura acotado [min, max]; > max no entra.
    Tamaño y stop se calculan con el precio de la señal (lo que sabe el
    ejecutor); R = entrada de referencia − stop.
  - Objetivo 2R; a +1R el stop pasa al precio de llenado; stop de tiempo a
    los N min si el máximo desde el llenado no tocó +0,5R; cierre a las
    15:50 NY (10 min antes, como la v1).
  - Stop y objetivo en la misma vela: cuenta el stop. En la vela del
    llenado solo se mira el stop.
  - Cartera: máx. posiciones simultáneas, máx. entradas por día, frenos
    diario y semanal sobre el P&L realizado vs el equity de apertura del
    día / del lunes. Equity para el tamaño = inicial + realizado.

NO REPRODUCIBLE (se imprime en el informe):
  - Halts: no hay fuente (A6). El filtro "sin halt en 30 min" NO se aplica:
    el resultado es optimista en ese punto.
  - Float, ETF y SPAC: los de HOY (Yahoo), no los de cada fecha.
  - Universo: los listados de HOY. Faltan los deslistados en el año
    (sesgo de supervivencia, optimista).
  - Slippage fijo; sin impacto de mercado ni rechazos de órdenes.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from estrategia_v2 import catalizador as cat
from estrategia_v2 import reglas
from estrategia_v2.config import ConfigV2
from estrategia_v2.reglas import Vela

from shadow_alpaca.backtest_v2 import datos
from shadow_alpaca.cliente import ErrorDatos

log = logging.getLogger("shadow_alpaca.backtest_v2.motor")

NY = ZoneInfo("America/New_York")
APERTURA = time(9, 30)
CIERRE = time(16, 0)
MINUTOS_ANTES_DEL_CIERRE = 10     # cierre de fin de día de la v1 (config del ejecutor)
PRE_GAP_FRACCION = 0.5            # ver EMBUDO, paso 2


@dataclass(frozen=True)
class Parametros:
    equity_inicial: float = 5_000.0
    slippage: float = 0.0015
    comision: float = 0.0
    # Regla de entrada del experimento (ver `entradas.py`): "orb" es la de
    # la estrategia (cierre de la vela de ruptura); e1/e2/e3 son variantes.
    entrada: str = "orb"


@dataclass
class Senal:
    ticker: str
    dia: date
    momento: datetime            # cierre de la vela de ruptura
    precio: float
    orb_bajo: float
    nivel: int
    gap: float
    rvol: float
    catalizador: str
    vela_bajo: float | None = None   # mínimo de la vela de ruptura (variante v1 del stop)
    orb_alto: float | None = None    # máximo del rango de apertura (entradas por retroceso)
    stop_base: float | None = None   # origen del stop fijado por la regla de entrada (e1, e3)
    llenado_fijo: bool = False       # True: se llena exactamente a `precio` (orden límite, e2)


def origen_stop(s: Senal, r) -> float:
    """De dónde sale el stop: lo que fijó la regla de entrada (`stop_base`),
    el mínimo de la vela de ruptura (variante v1) o el mínimo del rango de
    apertura (config)."""
    if s.stop_base is not None:
        return s.stop_base
    if r.stop_origen == "minimo_vela_ruptura" and s.vela_bajo is not None:
        return s.vela_bajo
    return s.orb_bajo


@dataclass
class Trade:
    senal: Senal
    cantidad: int
    entrada_ref: float
    stop_inicial: float
    objetivo: float
    llenado_t: datetime
    llenado: float
    salida_t: datetime
    salida: float
    motivo: str                  # stop | objetivo | breakeven | tiempo | cierre
    r: float
    pnl: float
    mfe_r: float
    mae_r: float


@dataclass
class Resultado:
    embudo: Counter = field(default_factory=Counter)
    descartes_senal: Counter = field(default_factory=Counter)
    no_entradas: Counter = field(default_factory=Counter)
    senales: list[Senal] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    curva: list[tuple[datetime, float]] = field(default_factory=list)
    clasificaciones: Counter = field(default_factory=Counter)
    sin_dato: Counter = field(default_factory=Counter)
    # Embudo etapa por etapa (informe): símbolo-días que sobreviven cada
    # filtro EN ORDEN, sobre las velas de la ventana. Ver `ETAPAS`.
    embudo_etapas: Counter = field(default_factory=Counter)
    # Distancia al stop (mínimo del rango de apertura vs precio de la señal)
    # de TODAS las señales y de las descartadas por el tope.
    stop_requerido: list[float] = field(default_factory=list)
    stop_descartado: list[float] = field(default_factory=list)
    sesiones: int = 0
    # Señales de la regla base (cierre de ruptura) antes de aplicar una
    # entrada alternativa, y las velas del día de cada símbolo-día con
    # señal: lo que el diagnóstico de entrada necesita.
    senales_base: list = field(default_factory=list)
    velas_de: dict = field(default_factory=dict)


# Orden del embudo etapa por etapa y qué fallo de `evaluar_ruptura` lo
# corta. Un símbolo-día sobrevive la etapa k si alguna vela de la ventana
# pasa las etapas 1..k. `spread` y `catalizador` se evalúan aparte (el
# spread cuesta una quote por minuto: se mira en las primeras
# `SPREAD_INTENTOS_EMBUDO` velas que pasaron las etapas previas).
ETAPAS = (
    ("gap", {"gap", "gap_sin_dato"}),
    ("rvol", {"rvol", "rvol_sin_dato"}),
    ("vwap", {"vwap", "vwap_sin_dato"}),
    ("ruptura_orb", {"sin_ruptura", "rango_sin_dato", "vol_ruptura", "vol_ruptura_sin_dato"}),
    ("spread", None),
    ("spy", {"indice", "indice_sin_dato"}),
    ("ventana", {"fuera_de_ventana"}),
)
SPREAD_INTENTOS_EMBUDO = 3


# ----------------------------------------------------------------- señales


def _velas_regulares(filas: list[list], cfg: ConfigV2) -> list[Vela]:
    return [v for v in datos.a_velas(filas) if reglas.es_regular(v, cfg.senal)]


def _acumulado_por_minuto(velas: list[Vela], cfg: ConfigV2, hasta: int) -> list[float] | None:
    """Volumen acumulado en cada minuto de sesión 0..hasta; None sin velas."""
    if not velas:
        return None
    por_min = [0.0] * (hasta + 1)
    for v in velas:
        m = reglas.minuto_de_sesion(v.t, cfg.senal)
        if 0 <= m <= hasta:
            por_min[m] += v.v
    acumulado, total = [], 0.0
    for x in por_min:
        total += x
        acumulado.append(total)
    return acumulado


def _minutos_de_ventana(cfg: ConfigV2, dia: date) -> int:
    fin = datetime.combine(dia, cfg.senal.ventana_fin, tzinfo=NY)
    inicio = datetime.combine(dia, cfg.senal.rango_apertura_inicio, tzinfo=NY)
    return (fin - inicio) // reglas.UN_MINUTO


def senal_del_dia(
    ticker: str, dia: date, velas_hoy: list[Vela], previos: list[list[float] | None],
    spy_hoy: list[Vela], gap: float, noticias_sim: list[datos.Noticia],
    clasificar, spread, cfg: ConfigV2, res: Resultado, meta_ok=None,
) -> Senal | None:
    """Primer cruce de la ventana que pasa TODO. `clasificar(noticia)` y
    `spread(momento)` son perezosos: solo se llaman si lo demás pasó."""
    s = cfg.senal
    horas = timedelta(hours=cfg.catalizador.horas_maximas)
    tope = _minutos_de_ventana(cfg, dia)
    hoy_acum = _acumulado_por_minuto(velas_hoy, cfg, tope)
    spy_por_t = {v.t: i for i, v in enumerate(spy_hoy)}
    peor = None
    alcanzadas: set[str] = set()     # etapas que el símbolo-día alcanzó (se cuentan una vez)
    etapas: set[str] = set()         # embudo etapa por etapa (informe)
    spread_cache: dict[datetime, float | None] = {}
    intentos_spread = 0

    def _contar_etapas() -> None:
        for etapa in alcanzadas:
            res.embudo[etapa] += 1
        for etapa in etapas:
            res.embudo_etapas[etapa] += 1

    def _spread(momento: datetime) -> float | None:
        if momento not in spread_cache:
            spread_cache[momento] = spread(momento)
        return spread_cache[momento]

    def _embudo(fallos: list[str], cierre: datetime) -> None:
        nonlocal intentos_spread
        f = set(fallos)
        for nombre, corta in ETAPAS:
            if nombre == "spread":
                if "spread" in etapas:
                    continue
                if intentos_spread >= SPREAD_INTENTOS_EMBUDO:
                    return
                intentos_spread += 1
                sp = _spread(cierre)
                if sp is None or sp > s.spread_max_pct:
                    return
            elif f & corta:
                return
            etapas.add(nombre)
    for i, vela in enumerate(velas_hoy):
        cierre = vela.t + reglas.UN_MINUTO
        if not (s.ventana_inicio <= reglas.hora_local(cierre, s) <= s.ventana_fin):
            continue
        m = reglas.minuto_de_sesion(vela.t, s)
        rv = None
        if hoy_acum is not None and 0 <= m <= tope:
            rv = reglas.rvol(hoy_acum[m], [p[m] if p else None for p in previos], s)
        j = spy_por_t.get(vela.t)
        indice_ok = None
        if j is not None:
            spy_vwap = reglas.vwap(spy_hoy[:j + 1])
            indice_ok = None if spy_vwap is None else spy_hoy[j].c > spy_vwap
        ev = reglas.evaluar_ruptura(velas_hoy, i, gap_oficial=gap, rvol_valor=rv, spread_pct=0.0,
                                    indice_sobre_vwap=indice_ok, cfg=cfg)
        _embudo(ev.fallos, cierre)
        if "sin_ruptura" in ev.fallos:
            continue
        if not (cfg.universo.precio_min <= vela.c <= cfg.universo.precio_max):
            ev.fallos.append("precio")
        vigentes = [n for n in noticias_sim if cierre - horas <= n.creada <= cierre]
        if any(cat.veto(n.titular, n.resumen, cfg.catalizador) for n in vigentes):
            ev.fallos.append("veto")
        nivel = None
        if not ev.fallos:
            alcanzadas.add("tecnica")
            if meta_ok is not None and not meta_ok():
                ev.fallos.append("metadata")
        if not ev.fallos:
            alcanzadas.add("metadata")
            clasif = [(n, clasificar(n)) for n in vigentes]
            nivel = cat.mejor_nivel([c for _, c in clasif], cfg.catalizador)
            if nivel is None:
                ev.fallos.append("sin_catalizador_operable")
        if not ev.fallos:
            alcanzadas.add("catalizador")
            sp = _spread(cierre)
            ev2 = reglas.evaluar_ruptura(velas_hoy, i, gap_oficial=gap, rvol_valor=rv, spread_pct=sp,
                                         indice_sobre_vwap=indice_ok, cfg=cfg)
            ev.fallos = ev2.fallos
        if not ev.fallos:
            # Esta vela pasó TODO, incluido el spread real: en el embudo
            # ordenado cuenta hasta catalizador aunque `_embudo` hubiera
            # agotado sus intentos de spread en velas anteriores. Así
            # "catalizador" nunca supera a "ventana".
            etapas.update({n for n, _ in ETAPAS} | {"catalizador"})
            elegida = next(n for n, c in clasif if cat.operable(c, cfg.catalizador) and c.nivel == nivel)
            _contar_etapas()
            return Senal(ticker, dia, cierre, vela.c, ev.orb_bajo, nivel, gap, rv, elegida.titular, vela.l, ev.orb_alto)
        peor = ev.fallos
    _contar_etapas()
    if peor:
        for f in peor:
            res.descartes_senal[f] += 1
    else:
        res.descartes_senal["sin_ruptura_en_ventana"] += 1
    return None


# ------------------------------------------------------------- ejecución


def _salida(senal: Senal, velas: list[Vela], cantidad: int, stop0: float, obj: float,
            cfg: ConfigV2, p: Parametros) -> Trade | None:
    r = cfg.riesgo
    tras = [v for v in velas if v.t >= senal.momento]
    if not tras:
        return None
    fv = tras[0]
    # Orden límite (e2): se llena exactamente al precio, sin slippage de entrada.
    llenado = senal.precio if senal.llenado_fijo else fv.o * (1 + p.slippage)
    r_unidad = senal.precio - stop0
    liquidacion = datetime.combine(senal.dia, CIERRE, tzinfo=NY) - timedelta(minutes=MINUTOS_ANTES_DEL_CIERRE)
    stop, en_be = stop0, False
    maximo, minimo = fv.h, fv.l
    limite_tiempo = fv.t + timedelta(minutes=r.stop_tiempo_minutos)
    salida_v, precio, motivo = None, None, None
    for k, v in enumerate(tras):
        if v.t >= liquidacion:
            salida_v, precio, motivo = v, v.o, "cierre"
            break
        if k > 0 and v.t >= limite_tiempo and maximo < llenado + r.stop_tiempo_r_minimo * r_unidad:
            salida_v, precio, motivo = v, v.o, "tiempo"
            break
        if v.l <= stop:
            # Si la vela abre por debajo del stop, se sale a la apertura (peor que el stop).
            salida_v, precio, motivo = v, min(stop, v.o), ("breakeven" if en_be else "stop")
            break
        if k > 0 and v.h >= obj:
            salida_v, precio, motivo = v, max(obj, v.o), "objetivo"
            break
        maximo, minimo = max(maximo, v.h), min(minimo, v.l)
        if not en_be and maximo >= llenado + r.breakeven_en_r * r_unidad:
            stop, en_be = llenado, True
    if salida_v is None:
        salida_v = tras[-1]
        precio, motivo = salida_v.c, "cierre"
    maximo, minimo = max(maximo, salida_v.h), min(minimo, salida_v.l)
    ejec = precio * (1 - p.slippage)
    pnl = (ejec - llenado) * cantidad - 2 * p.comision
    return Trade(senal, cantidad, senal.precio, stop0, obj, fv.t, llenado, salida_v.t, ejec, motivo,
                 (ejec - llenado) / r_unidad, pnl, (maximo - llenado) / r_unidad, (llenado - minimo) / r_unidad)


def simular(senales: list[Senal], velas_de: dict[tuple[str, date], list[Vela]], cfg: ConfigV2,
            p: Parametros, res: Resultado) -> None:
    r = cfg.riesgo
    equity = p.equity_inicial
    res.curva.append((datetime.min.replace(tzinfo=NY), equity))
    # Etapa "stop" del embudo y distribución del stop requerido, sobre
    # TODAS las señales (antes de los límites de cartera).
    for s in senales:
        if s.precio > 0:
            d = (s.precio - origen_stop(s, r)) / s.precio
            res.stop_requerido.append(d)
            if reglas.stop_inicial(s.precio, origen_stop(s, r), r) is None:
                res.stop_descartado.append(d)
            else:
                res.embudo_etapas["stop"] += 1
    abiertos: list[Trade] = []
    dia_actual, semana_actual = None, None
    equity_dia = equity_semana = equity
    entradas_dia = 0
    for s in sorted(senales, key=lambda x: x.momento):
        # Cerrar lo que ya salió antes de esta señal: el realizado cuenta.
        for t in sorted([t for t in abiertos if t.salida_t <= s.momento], key=lambda t: t.salida_t):
            equity += t.pnl
            res.curva.append((t.salida_t, equity))
            abiertos.remove(t)
        if s.dia != dia_actual:
            for t in sorted(abiertos, key=lambda t: t.salida_t):
                equity += t.pnl
                res.curva.append((t.salida_t, equity))
            abiertos = []
            dia_actual, entradas_dia, equity_dia = s.dia, 0, equity
            semana = s.dia.isocalendar()[:2]
            if semana != semana_actual:
                semana_actual, equity_semana = semana, equity
        if equity / equity_dia - 1 <= r.freno_diario_pct:
            res.no_entradas["freno_diario"] += 1
            continue
        if equity / equity_semana - 1 <= r.freno_semanal_pct:
            res.no_entradas["freno_semanal"] += 1
            continue
        if len(abiertos) >= r.max_posiciones:
            res.no_entradas["max_posiciones"] += 1
            continue
        if entradas_dia >= r.max_entradas_dia:
            res.no_entradas["max_entradas_dia"] += 1
            continue
        stop0 = reglas.stop_inicial(s.precio, origen_stop(s, r), r)
        if stop0 is None:
            res.no_entradas["stop_mayor_al_maximo"] += 1
            continue
        efectivo = equity - sum(t.llenado * t.cantidad for t in abiertos)
        cantidad = reglas.tamano(equity, efectivo, s.precio * (1 + p.slippage), stop0, s.nivel, r)
        if cantidad < 1:
            res.no_entradas["tamano_cero"] += 1
            continue
        trade = _salida(s, velas_de.get((s.ticker, s.dia), []), cantidad, stop0,
                        reglas.objetivo(s.precio, stop0, r), cfg, p)
        if trade is None:
            res.no_entradas["sin_velas_tras_senal"] += 1
            continue
        entradas_dia += 1
        abiertos.append(trade)
        res.trades.append(trade)
    for t in sorted(abiertos, key=lambda t: t.salida_t):
        equity += t.pnl
        res.curva.append((t.salida_t, equity))


# ------------------------------------------------------------------ corrida


def sesiones_de(spy_diarias: list[list]) -> list[date]:
    return [datetime.fromisoformat(f[0].replace("Z", "+00:00")).astimezone(NY).date() for f in spy_diarias]


@dataclass
class Candidatos:
    """Pasos 1-4 del embudo: sesiones, diarias y símbolo-días con gap
    oficial y sin acción corporativa. Lo comparten el motor y los planes B."""

    tickers: list[str]
    todas: list[date]                 # sesiones de SPY (incluye el arranque para el RVOL)
    sesiones: list[date]
    diarias: dict[str, list[list]]
    por_dia: dict[date, dict[str, float]]   # día -> {ticker: gap oficial}


def candidatos_gap(cfg: ConfigV2, cliente, cache: datos.Cache, universo: list, desde: date, hasta: date,
                   res: Resultado) -> Candidatos:
    s = cfg.senal
    permitidos = [u for u in universo if u.bolsa in cfg.universo.exchanges
                  and not (cfg.universo.excluir_etf and u.es_etf)]
    tickers = [u.ticker for u in permitidos]
    res.embudo["universo"] = len(tickers)
    arranque = desde - timedelta(days=math.ceil(s.rvol_dias * 7 / 5) + 14)
    spy = datos.diarias(cliente, cache, [s.indice_referencia], arranque, hasta)[s.indice_referencia]
    todas = sesiones_de(spy)
    sesiones = [d for d in todas if desde <= d <= hasta]
    diarias = datos.diarias(cliente, cache, tickers, arranque, hasta)
    log.info("diarias: %d símbolos, %d sesiones", len(diarias), len(sesiones))

    # Pasos 2-4. Un índice por símbolo: fecha -> (apertura, cierre previo).
    aperturas: dict[date, list[tuple[str, float, float]]] = {}
    for t, filas in diarias.items():
        previo = None
        for f in filas:
            fecha = datetime.fromisoformat(f[0].replace("Z", "+00:00")).astimezone(NY).date()
            if previo is not None:
                aperturas.setdefault(fecha, []).append((t, f[1], previo))
            previo = f[4]
    por_dia: dict[date, dict[str, float]] = {}
    for d in sesiones:
        pre = [t for t, o_hoy, c_prev in aperturas.get(d, [])
               if c_prev > 0 and o_hoy / c_prev - 1 >= s.gap_min_pct * PRE_GAP_FRACCION
               and cfg.universo.precio_min <= o_hoy <= cfg.universo.precio_max]
        res.embudo["pre_gap"] += len(pre)
        if not pre:
            continue
        subs = datos.subastas_del_dia(cache, pre, d)
        for t in pre:
            g = datos.gap_oficial(subs.get(t, {}), d)
            if g is None:
                res.sin_dato["gap_oficial"] += 1
            elif g >= s.gap_min_pct:
                por_dia.setdefault(d, {})[t] = g
        res.embudo["gap_oficial"] += len(por_dia.get(d, {}))
    candidatos = sorted({t for v in por_dia.values() for t in v})
    acciones = datos.acciones_corporativas(cache, candidatos, desde, hasta) if candidatos else set()
    for d in list(por_dia):
        for t in list(por_dia[d]):
            if (t.upper(), d.isoformat()) in acciones:
                del por_dia[d][t]
        res.embudo["sin_accion_corporativa"] += len(por_dia[d])
    return Candidatos(tickers, todas, sesiones, diarias, por_dia)


def correr(cfg: ConfigV2, cliente, cache: datos.Cache, universo: list, desde: date, hasta: date,
           clasificador, p: Parametros, proveedor_meta=None) -> Resultado:
    res = Resultado()
    s = cfg.senal
    cand = candidatos_gap(cfg, cliente, cache, universo, desde, hasta, res)
    todas, sesiones, por_dia = cand.todas, cand.sesiones, cand.por_dia

    # Pasos 5-9.
    u = cfg.universo
    meta_cache: dict[str, bool] = {}

    def meta_ok(t: str) -> bool:
        if t not in meta_cache:
            m = datos.metadata(cache, [t], proveedor_meta).get(t)
            fl = m.get("float") if m else None
            if m is None or fl is None:
                res.sin_dato["float"] += 1
            meta_cache[t] = (m is not None and fl is not None and u.float_min <= fl <= u.float_max
                             and not (u.excluir_etf and m.get("es_etf"))
                             and not (u.excluir_spac and m.get("es_spac")))
        return meta_cache[t]

    velas_de: dict[tuple[str, date], list[Vela]] = {}
    horas = timedelta(hours=cfg.catalizador.horas_maximas)
    for n_d, d in enumerate(sorted(por_dia)):
        cands = por_dia[d]
        if not cands:
            continue
        fin_ventana = datos.en_ny(d, s.ventana_fin)
        try:
            nots = datos.noticias(cliente, cache, list(cands), fin_ventana - horas, fin_ventana)
        except ErrorDatos:
            res.sin_dato["noticias"] += len(cands)
            continue
        por_sim = {t: [n for n in nots if t in n.simbolos] for t in cands}
        con_noticia = [t for t in cands if por_sim[t]]
        res.embudo["con_noticias"] += len(con_noticia)
        if not con_noticia:
            continue
        idx = todas.index(d)
        previas = todas[max(0, idx - s.rvol_dias):idx]
        hoy_full = datos.minutos(cliente, cache, con_noticia + [s.indice_referencia], d, APERTURA, CIERRE)
        prev_min = {f: datos.minutos(cliente, cache, con_noticia, f, APERTURA, s.ventana_fin) for f in previas}
        premercado = (datos.minutos(cliente, cache, con_noticia, d, time(4, 0), APERTURA)
                      if p.entrada == "e3" else {})
        spy_hoy = _velas_regulares(hoy_full.get(s.indice_referencia, []), cfg)
        tope = _minutos_de_ventana(cfg, d)
        for t in con_noticia:
            velas_hoy = _velas_regulares(hoy_full.get(t, []), cfg)
            if not velas_hoy:
                res.sin_dato["velas_hoy"] += 1
                continue
            velas_de[(t, d)] = velas_hoy
            previos = [_acumulado_por_minuto(_velas_regulares(prev_min[f].get(t, []), cfg), cfg, tope)
                       for f in previas]
            clasificar = lambda n: clasificador.clasificar(n, cfg, res)                 # noqa: E731
            spread = lambda m, t=t: datos.spread_pct(cliente, cache, t, m)              # noqa: E731
            if p.entrada == "e3":
                from shadow_alpaca.backtest_v2 import entradas
                senal = entradas.senal_e3_del_dia(
                    t, d, velas_hoy, datos.a_velas(premercado.get(t, [])), previos, spy_hoy, cands[t], por_sim[t],
                    clasificar, spread, cfg, res, meta_ok=lambda t=t: meta_ok(t))
            else:
                senal = senal_del_dia(
                    t, d, [v for v in velas_hoy if v.t < fin_ventana], previos, spy_hoy, cands[t], por_sim[t],
                    clasificar, spread, cfg, res, meta_ok=lambda t=t: meta_ok(t))
                if senal is not None and p.entrada != "orb":
                    from shadow_alpaca.backtest_v2 import entradas
                    res.senales_base.append(senal)
                    senal = entradas.transformar(p.entrada, senal, velas_hoy, cfg)
                    if senal is None:
                        res.no_entradas[f"sin_entrada_{p.entrada}"] += 1
            if senal is not None:
                res.senales.append(senal)
                res.embudo["senal"] += 1
        if (n_d + 1) % 20 == 0:
            log.info("señales: %d días procesados, %d señales", n_d + 1, len(res.senales))
    simular(res.senales, velas_de, cfg, p, res)
    res.embudo["trades"] = len(res.trades)
    res.sesiones = len(sesiones)
    res.velas_de = velas_de
    return res
