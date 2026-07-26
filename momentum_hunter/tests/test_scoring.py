"""Pruebas del score compuesto -- verifica cada sub-score por separado y
que `puntuar` re-normaliza sobre los pesos disponibles cuando falta un
factor (mismo principio que `screener.scoring.puntuar`), sin castigar
con 0 lo que no se pudo calcular."""

from __future__ import annotations

from momentum_hunter import scoring as sc
from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.config import CONFIG
from momentum_hunter.models import FactoresMomentum, Metadata


def _factores(**kwargs) -> FactoresMomentum:
    return FactoresMomentum(**kwargs)


def test_momentum_score_none_sin_ningun_factor():
    assert sc.momentum_score(_factores()) is None


def test_momentum_score_maximo_con_todo_a_favor():
    f = _factores(rvol=50.0, gap_pct=0.5, breakout_20d=True, distancia_max_52s=1.0,
                 rsi=80.0, macd=1.0, macd_signal=0.5)
    assert sc.momentum_score(f) == 100.0


def test_momentum_score_breakout_sin_otro_dato_no_cuenta():
    # `breakout_20d` por sí solo (sin ningún otro factor real) no debe
    # producir un score -- ver el comentario en scoring.momentum_score.
    assert sc.momentum_score(_factores(breakout_20d=True)) is None


def test_momentum_score_breakout_suma_puntos():
    con_breakout = sc.momentum_score(_factores(rvol=1.0, breakout_20d=True))
    sin_breakout = sc.momentum_score(_factores(rvol=1.0, breakout_20d=False))
    assert con_breakout > sin_breakout


def test_catalyst_score_cero_sin_catalizador():
    assert sc.catalyst_score(None) == 0.0


def test_catalyst_score_usa_fuerza_por_tipo():
    fda = sc.catalyst_score(Catalizador(tipo="fda", titular="x", fuente="Reuters"))
    buyback = sc.catalyst_score(Catalizador(tipo="buyback", titular="x", fuente="Reuters"))
    assert fda > buyback


def test_catalyst_score_bonus_por_fuentes_adicionales():
    sin_bonus = sc.catalyst_score(Catalizador(tipo="rumor", titular="x", fuente="A"))
    con_bonus = sc.catalyst_score(Catalizador(
        tipo="rumor", titular="x", fuente="A", fuentes_adicionales=("B", "C")))
    assert con_bonus > sin_bonus


def test_catalyst_score_no_confirmado_es_cero():
    c = Catalizador(tipo="fda", titular="x", fuente="Reuters", confirmado=False)
    assert sc.catalyst_score(c) == 0.0


def test_liquidez_score_escalones():
    alta = sc.liquidez_score(precio=10.0, volumen_promedio=5_000_000, cfg=CONFIG)
    baja = sc.liquidez_score(precio=1.0, volumen_promedio=300_000, cfg=CONFIG)
    assert alta > baja


def test_liquidez_score_cero_sin_volumen():
    assert sc.liquidez_score(precio=10.0, volumen_promedio=None, cfg=CONFIG) == 0.0


def test_riesgo_score_penaliza_atr_extremo():
    sano = sc._riesgo_volatilidad(_factores(atr=0.5), precio=10.0)     # 5% -- dentro de banda
    extremo = sc._riesgo_volatilidad(_factores(atr=5.0), precio=10.0)  # 50% -- muy fuera de banda
    assert sano == 100.0
    assert extremo < sano


def test_riesgo_score_float_bajo_puntua_menos_que_float_alto():
    bajo = sc._riesgo_float(Metadata(ticker="A", shares_float=1_000_000))
    alto = sc._riesgo_float(Metadata(ticker="B", shares_float=200_000_000))
    assert alto > bajo


def test_puntuar_renormaliza_cuando_falta_momentum():
    # Sin ningún factor de momentum disponible, el score se calcula solo
    # sobre catalizador/liquidez/riesgo -- nunca se castiga con 0.
    f = _factores()  # todo None
    c = Catalizador(tipo="fda", titular="x", fuente="Reuters")
    p = sc.puntuar("TST", 10.0, 1_000_000, f, c, Metadata(ticker="TST"), CONFIG)
    assert "momentum" not in p.sub
    assert p.score_total > 0.0


def test_puntuar_score_alto_con_todo_a_favor():
    f = _factores(rvol=8.0, gap_pct=0.15, breakout_20d=True, distancia_max_52s=0.99,
                 rsi=65.0, macd=1.0, macd_signal=0.5, atr=0.5)
    c = Catalizador(tipo="fda", titular="x", fuente="Reuters", fuentes_adicionales=("B",))
    meta = Metadata(ticker="TST", shares_float=30_000_000)
    p = sc.puntuar("TST", 10.0, 5_000_000, f, c, meta, CONFIG)
    assert p.score_total >= 85.0
