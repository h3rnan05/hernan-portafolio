"""Pruebas de detección de patrones (Prompt 4, pregunta 4) -- los seis
patrones de Ross Cameron sobre velas intradía. Verifica cada patrón por
separado y el orden de prioridad cuando varios podrían aplicar a la vez
(nunca se mezclan dos etiquetas)."""

from __future__ import annotations

from momentum_hunter import classification as clf
from momentum_hunter.models import BarraIntradia, FactoresIntradia


def _bi(closes, highs=None, lows=None, vols=None, ticker="TST") -> BarraIntradia:
    n = len(closes)
    highs = highs or [c * 1.002 for c in closes]
    lows = lows or [c * 0.998 for c in closes]
    vols = vols or [10_000.0] * n
    marcas = [f"2026-07-26T13:{30 + i:02d}:00+00:00" for i in range(n)]
    return BarraIntradia(ticker, marcas, closes, closes, highs, lows, vols)


def _factores(**kwargs) -> FactoresIntradia:
    return FactoresIntradia(**kwargs)


def test_high_tight_flag_exige_impulso_grande_y_bandera_angosta():
    # Impulso de +60% entre -11 y -4, luego 3 velas casi planas.
    impulso = [1.0 + i * (0.60 / 7) for i in range(8)]  # índices -11..-4
    bandera = [impulso[-1]] * 3
    closes = impulso + bandera
    bi = _bi(closes)
    assert clf._es_high_tight_flag(bi) is True


def test_high_tight_flag_falso_si_la_bandera_es_ancha():
    impulso = [1.0 + i * (0.60 / 7) for i in range(8)]
    bandera = [impulso[-1] * 1.10, impulso[-1] * 0.90, impulso[-1] * 1.05]  # rango ancho
    bi = _bi(impulso + bandera)
    assert clf._es_high_tight_flag(bi) is False


def test_gap_and_go_exige_gap_ruptura_y_volumen():
    f = _factores(gap_pct=0.10, maximo_premarket=5.0, precio_actual=5.20, rvol_actual=3.0)
    assert clf._es_gap_and_go(f) is True


def test_gap_and_go_falso_sin_romper_premarket():
    f = _factores(gap_pct=0.10, maximo_premarket=5.0, precio_actual=4.90, rvol_actual=3.0)
    assert clf._es_gap_and_go(f) is False


def test_opening_range_breakout_exige_romper_el_rango_con_volumen():
    f = _factores(rango_apertura_max=5.0, precio_actual=5.10, rvol_actual=2.5)
    assert clf._es_opening_range_breakout(f) is True


def test_opening_range_breakout_falso_sin_volumen():
    f = _factores(rango_apertura_max=5.0, precio_actual=5.10, rvol_actual=1.0)
    assert clf._es_opening_range_breakout(f) is False


def test_bull_flag_impulso_bandera_angosta_y_volumen_decayendo():
    # 8 velas: impulso de +6% entre -8 y -5, luego 3 velas de bandera
    # angosta con menos volumen que el impulso.
    closes = [1.0, 1.01, 1.02, 1.06, 1.07, 1.08, 1.081, 1.079]
    vols = [50_000] * 5 + [10_000] * 3
    bi = _bi(closes, vols=vols)
    assert clf._es_bull_flag(bi) is True


def test_bull_flag_falso_sin_impulso_previo():
    plano = [1.0] * 8
    bi = _bi(plano)
    assert clf._es_bull_flag(bi) is False


def test_micro_pullback_impulso_pullback_y_recuperacion():
    closes = [1.0, 1.02, 1.05, 1.03, 1.04]   # impulso, impulso, pullback, recupera
    vols = [10_000, 10_000, 15_000, 5_000, 8_000]
    bi = _bi(closes, vols=vols)
    f = _factores(ema9=1.00)
    assert clf._es_micro_pullback(bi, f) is True


def test_micro_pullback_falso_si_pierde_ema9():
    closes = [1.0, 1.02, 1.05, 1.03, 1.04]
    vols = [10_000, 10_000, 15_000, 5_000, 8_000]
    bi = _bi(closes, vols=vols)
    f = _factores(ema9=2.00)   # muy por encima -- perdió la EMA9
    assert clf._es_micro_pullback(bi, f) is False


def test_trend_continuation_sobre_vwap_y_ema9():
    closes = [1.00, 1.01, 1.02, 1.03, 1.04, 1.05]
    bi = _bi(closes)
    f = _factores(vwap=1.00, ema9=1.00)
    assert clf._es_trend_continuation(bi, f) is True


def test_trend_continuation_falso_bajo_vwap():
    closes = [1.00, 1.01, 1.02, 1.03, 1.04, 1.05]
    bi = _bi(closes)
    f = _factores(vwap=1.20, ema9=1.00)
    assert clf._es_trend_continuation(bi, f) is False


def test_detectar_patron_prioriza_high_tight_flag_sobre_bull_flag():
    # Cumple las condiciones (más laxas) de bull flag Y las de HTF -- debe ganar HTF.
    impulso = [1.0 + i * (0.60 / 7) for i in range(8)]
    bandera = [impulso[-1]] * 3
    bi = _bi(impulso + bandera)
    f = _factores()
    assert clf.detectar_patron(bi, f) == "high_tight_flag"


def test_detectar_patron_ninguno_aplica_devuelve_none():
    bi = _bi([1.0] * 10)
    f = _factores()
    assert clf.detectar_patron(bi, f) is None


def test_etiqueta_incluye_emoji():
    assert clf.etiqueta("gap_and_go") == "🚀 GAP AND GO"
