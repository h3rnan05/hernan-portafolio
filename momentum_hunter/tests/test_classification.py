"""Pruebas de clasificación de tipo de oportunidad -- verifica cada
patrón por separado y el orden de prioridad cuando varios podrían
aplicar a la vez (nunca se mezclan dos etiquetas)."""

from __future__ import annotations

from momentum_hunter import classification as clf
from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.models import FactoresMomentum, Metadata


def _factores(**kwargs) -> FactoresMomentum:
    base = dict(gap_pct=0.0, rvol=1.0, breakout_20d=False, distancia_max_52s=0.5,
                ema20=10.0, ema50=10.0, rsi=50.0, macd=0.0, macd_signal=0.0, macd_hist=0.0)
    base.update(kwargs)
    return FactoresMomentum(**base)


def _meta(**kwargs) -> Metadata:
    base = dict(ticker="TST")
    base.update(kwargs)
    return Metadata(**base)


def test_short_squeeze_exige_float_bajo_short_alto_y_rvol():
    f = _factores(rvol=5.0)
    m = _meta(shares_float=3_000_000, short_pct_float=0.30)
    assert clf.tipo_oportunidad(10.0, f, None, m) == "short_squeeze"


def test_short_squeeze_no_dispara_con_float_alto():
    f = _factores(rvol=5.0)
    m = _meta(shares_float=100_000_000, short_pct_float=0.30)
    assert clf.tipo_oportunidad(10.0, f, None, m) != "short_squeeze"


def test_earnings_play_exige_catalizador_earnings_y_gap_grande():
    f = _factores(gap_pct=0.12)
    c = Catalizador(tipo="earnings", titular="Beats estimates", fuente="Reuters")
    assert clf.tipo_oportunidad(10.0, f, c, _meta()) == "earnings_play"


def test_earnings_play_no_dispara_con_gap_pequeno():
    f = _factores(gap_pct=0.01)
    c = Catalizador(tipo="earnings", titular="Beats estimates", fuente="Reuters")
    assert clf.tipo_oportunidad(10.0, f, c, _meta()) != "earnings_play"


def test_news_momentum_con_catalizador_generico_y_rvol_alto():
    f = _factores(rvol=4.0, gap_pct=0.01)
    c = Catalizador(tipo="contrato", titular="Awarded contract", fuente="Reuters")
    assert clf.tipo_oportunidad(10.0, f, c, _meta()) == "news_momentum"


def test_breakout_exige_las_tres_condiciones():
    f = _factores(breakout_20d=True, distancia_max_52s=0.99, rvol=2.5)
    assert clf.tipo_oportunidad(10.0, f, None, _meta()) == "breakout"


def test_breakout_no_dispara_sin_rvol_suficiente():
    f = _factores(breakout_20d=True, distancia_max_52s=0.99, rvol=1.0)
    assert clf.tipo_oportunidad(10.0, f, None, _meta()) != "breakout"


def test_reversal_exige_rsi_bajo_y_macd_cruzando_al_alza():
    f = _factores(rsi=35.0, macd=0.5, macd_signal=0.1)
    assert clf.tipo_oportunidad(10.0, f, None, _meta()) == "reversal"


def test_trend_continuation_pullback_sano_sobre_ema20():
    f = _factores(ema20=10.0, ema50=9.0, rsi=50.0)
    assert clf.tipo_oportunidad(10.2, f, None, _meta()) == "trend_continuation"


def test_fallback_sin_ninguna_condicion_es_news_momentum():
    f = _factores(rvol=None, gap_pct=None, distancia_max_52s=None, ema20=None, ema50=None, rsi=None)
    assert clf.tipo_oportunidad(10.0, f, None, _meta()) == "news_momentum"


def test_prioridad_short_squeeze_sobre_breakout():
    # Cumple ambos patrones a la vez -- debe ganar short_squeeze (mayor prioridad).
    f = _factores(rvol=5.0, breakout_20d=True, distancia_max_52s=0.99)
    m = _meta(shares_float=3_000_000, short_pct_float=0.30)
    assert clf.tipo_oportunidad(10.0, f, None, m) == "short_squeeze"


def test_clasificar_devuelve_etiqueta_con_emoji():
    f = _factores(breakout_20d=True, distancia_max_52s=0.99, rvol=2.5)
    assert clf.clasificar(10.0, f, None, _meta()) == "🔥 BREAKOUT"
