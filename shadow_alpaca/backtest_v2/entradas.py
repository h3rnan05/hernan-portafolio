"""Reglas de entrada alternativas del backtest v2 (experimentos, no estrategia).

Pedidas el 2026-09-29 tras el diagnóstico de que el trade típico va más
en contra que a favor desde el cierre de la vela de ruptura:

  e1  Retroceso: tras la señal base, esperar a que el precio vuelva al
      máximo del ORB o al VWAP (el más alto de los dos en el minuto de la
      señal) y que una vela de 1 min cierre por encima; entrada al cierre
      de esa vela, stop = mínimo del retroceso (acotado 1,5–4 % por
      `stop_inicial`), espera máxima 20 min. Sin retroceso, sin trade.
  e2  Orden límite en el máximo del ORB tras la señal base, válida 15 min:
      si alguna vela toca ese precio, se llena exactamente ahí (sin
      slippage de entrada); stop = mínimo del ORB acotado. Sin toque, sin
      trade.
  e3  Ruptura del máximo del premercado (04:00–09:29) entre las 09:31 y
      las 09:45, con volumen de la vela ≥ 1,5× el promedio de las velas
      regulares previas del día (hace falta al menos una); stop = mínimo
      de los primeros 3 minutos (09:30–09:32) acotado. Los demás filtros
      (gap, RVOL, VWAP, SPY, precio, spread, veto, metadata, catalizador)
      son los de la base, evaluados en esa vela.

Todas devuelven una `Senal` que el simulador ejecuta con las mismas
reglas de salida que la base (objetivo 2R, breakeven, stop de tiempo,
cierre 15:50).
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from estrategia_v2 import catalizador as cat
from estrategia_v2 import reglas
from estrategia_v2.reglas import Vela

from shadow_alpaca.backtest_v2.motor import Senal, _acumulado_por_minuto, _minutos_de_ventana

ESPERA_E1 = timedelta(minutes=20)
VALIDEZ_E2 = timedelta(minutes=15)
E3_DESDE, E3_HASTA = time(9, 31), time(9, 45)     # hora de cierre de la vela
E3_MINUTOS_STOP = 3


def _regulares_hasta(velas: list[Vela], momento: datetime, cfg) -> list[Vela]:
    return [v for v in velas if reglas.es_regular(v, cfg.senal) and v.t < momento]


def nivel_retroceso(s: Senal, velas: list[Vela], cfg) -> float | None:
    """El más alto entre el máximo del ORB y el VWAP en el minuto de la señal."""
    if s.orb_alto is None:
        return None
    vw = reglas.vwap(_regulares_hasta(velas, s.momento, cfg))
    return max(s.orb_alto, vw) if vw is not None else s.orb_alto


def transformar_e1(s: Senal, velas: list[Vela], cfg) -> Senal | None:
    nivel = nivel_retroceso(s, velas, cfg)
    if nivel is None:
        return None
    minimo, tocado = None, False
    for v in velas:
        if v.t < s.momento or v.t >= s.momento + ESPERA_E1:
            continue
        minimo = v.l if minimo is None else min(minimo, v.l)
        if v.l <= nivel:
            tocado = True
        if tocado and v.c > nivel:
            return Senal(s.ticker, s.dia, v.t + reglas.UN_MINUTO, v.c, s.orb_bajo, s.nivel, s.gap, s.rvol, s.catalizador,
                         v.l, s.orb_alto, stop_base=minimo)
    return None


def transformar_e2(s: Senal, velas: list[Vela], cfg) -> Senal | None:
    if s.orb_alto is None:
        return None
    for v in velas:
        if v.t < s.momento or v.t >= s.momento + VALIDEZ_E2:
            continue
        if v.l <= s.orb_alto:
            return Senal(s.ticker, s.dia, v.t, s.orb_alto, s.orb_bajo, s.nivel, s.gap, s.rvol, s.catalizador,
                         s.vela_bajo, s.orb_alto, stop_base=s.orb_bajo, llenado_fijo=True)
    return None


TRANSFORMACIONES = {"e1": transformar_e1, "e2": transformar_e2}


def transformar(entrada: str, s: Senal, velas: list[Vela], cfg) -> Senal | None:
    if entrada not in TRANSFORMACIONES:
        raise KeyError(f"entrada desconocida: {entrada}")
    return TRANSFORMACIONES[entrada](s, velas, cfg)


# ------------------------------------------------------------------ e3


def senal_e3_del_dia(ticker, dia, velas_hoy, premercado, previos, spy_hoy, gap, noticias_sim, clasificar, spread,
                     cfg, res, meta_ok=None) -> Senal | None:
    """Primera vela entre 09:31 y 09:45 (cierre) que rompe el máximo del
    premercado con volumen y pasa los filtros de la base."""
    s = cfg.senal
    pm_alto = max((v.h for v in premercado), default=None)
    if pm_alto is None:
        res.descartes_senal["sin_premercado"] += 1
        return None
    regulares = [v for v in velas_hoy if reglas.es_regular(v, s)]
    if not regulares:
        return None
    horas = timedelta(hours=cfg.catalizador.horas_maximas)
    tope = _minutos_de_ventana(cfg, dia)
    hoy_acum = _acumulado_por_minuto(regulares, cfg, tope)
    spy_por_t = {v.t: i for i, v in enumerate(spy_hoy)}
    stop_base = min(v.l for v in regulares[:E3_MINUTOS_STOP])
    peor = None
    previa = None
    for i, vela in enumerate(regulares):
        cierre = vela.t + reglas.UN_MINUTO
        hora = reglas.hora_local(cierre, s)
        if hora < E3_DESDE:
            previa = vela
            continue
        if hora > E3_HASTA:
            break
        fallos: list[str] = []
        cruza = vela.c > pm_alto and (previa is None or previa.c <= pm_alto)
        previa = vela
        if not cruza:
            continue
        anteriores = [v.v for v in regulares[:i]]
        if not anteriores:
            fallos.append("vol_ruptura_sin_dato")
        elif vela.v < s.vela_ruptura_vol_min_x * (sum(anteriores) / len(anteriores)):
            fallos.append("vol_ruptura")
        if gap is None:
            fallos.append("gap_sin_dato")
        elif gap < s.gap_min_pct:
            fallos.append("gap")
        m = reglas.minuto_de_sesion(vela.t, s)
        rv = None
        if hoy_acum is not None and 0 <= m <= tope:
            rv = reglas.rvol(hoy_acum[m], [p[m] if p else None for p in previos], s)
        if rv is None:
            fallos.append("rvol_sin_dato")
        elif rv < s.rvol_min:
            fallos.append("rvol")
        vw = reglas.vwap(regulares[:i + 1])
        if s.precio_sobre_vwap and (vw is None or vela.c <= vw):
            fallos.append("vwap" if vw is not None else "vwap_sin_dato")
        j = spy_por_t.get(vela.t)
        if s.indice_sobre_vwap:
            if j is None:
                fallos.append("indice_sin_dato")
            else:
                spy_vwap = reglas.vwap(spy_hoy[:j + 1])
                if spy_vwap is None or spy_hoy[j].c <= spy_vwap:
                    fallos.append("indice")
        if not (cfg.universo.precio_min <= vela.c <= cfg.universo.precio_max):
            fallos.append("precio")
        vigentes = [n for n in noticias_sim if cierre - horas <= n.creada <= cierre]
        if any(cat.veto(n.titular, n.resumen, cfg.catalizador) for n in vigentes):
            fallos.append("veto")
        nivel = None
        if not fallos and meta_ok is not None and not meta_ok():
            fallos.append("metadata")
        if not fallos:
            clasif = [(n, clasificar(n)) for n in vigentes]
            nivel = cat.mejor_nivel([c for _, c in clasif], cfg.catalizador)
            if nivel is None:
                fallos.append("sin_catalizador_operable")
        if not fallos:
            sp = spread(cierre)
            if sp is None:
                fallos.append("spread_sin_dato")
            elif sp > s.spread_max_pct:
                fallos.append("spread")
        if not fallos:
            elegida = next(n for n, c in clasif if cat.operable(c, cfg.catalizador) and c.nivel == nivel)
            res.embudo["catalizador"] += 1
            return Senal(ticker, dia, cierre, vela.c, stop_base, nivel, gap, rv, elegida.titular, vela.l, pm_alto,
                         stop_base=stop_base)
        peor = fallos
    if peor:
        for f in peor:
            res.descartes_senal[f] += 1
    else:
        res.descartes_senal["sin_ruptura_premercado"] += 1
    return None
