"""Pruebas de los factores de momentum -- sin red, sin pandas. Verifica
que cada función responde a la definición exacta que documenta (gap
sobre cierre de ayer, RVOL excluye la barra de hoy del promedio,
breakout exige superar el máximo de las N sesiones ANTERIORES, etc.)."""

from __future__ import annotations

from momentum_hunter.factors import momentum as mom
from momentum_hunter.models import Barras


def _barras(closes, opens=None, highs=None, lows=None, vols=None, ticker="TST"):
    n = len(closes)
    opens = opens or closes
    highs = highs or [c * 1.01 for c in closes]
    lows = lows or [c * 0.99 for c in closes]
    vols = vols or [1_000_000.0] * n
    return Barras(ticker, [str(i) for i in range(n)], opens, closes, highs, lows, vols)


def test_gap_pct_usa_cierre_de_ayer():
    b = _barras(closes=[10.0, 10.0], opens=[10.0, 11.0])
    assert mom.gap_pct(b) == 0.1


def test_gap_pct_none_con_menos_de_dos_barras():
    assert mom.gap_pct(_barras(closes=[10.0])) is None


def test_rvol_excluye_la_barra_de_hoy_del_promedio():
    vols = [1_000_000.0] * 20 + [5_000_000.0]
    b = _barras(closes=[10.0] * 21, vols=vols)
    assert mom.rvol(b, ventana=20) == 5.0


def test_rvol_none_sin_suficiente_historia():
    assert mom.rvol(_barras(closes=[10.0] * 5), ventana=20) is None


def test_breakout_nd_exige_superar_las_sesiones_anteriores():
    # 21 cierres subiendo, el último hace nuevo máximo de las 20 anteriores.
    closes = [10.0 + i * 0.1 for i in range(20)] + [12.5]
    b = _barras(closes=closes)
    assert mom.breakout_nd(b, 20) is True


def test_breakout_nd_false_si_no_supera_el_maximo_previo():
    closes = [10.0 + i * 0.1 for i in range(21)]
    closes[-1] = closes[-2]  # no hace nuevo máximo
    b = _barras(closes=closes)
    assert mom.breakout_nd(b, 20) is False


def test_distancia_maximo_52s_en_maximos_es_uno():
    highs = [10.0] * 300
    highs[-1] = 15.0
    b = _barras(closes=[15.0] * 300, highs=highs)
    assert mom.distancia_maximo_52s(b) == 1.0


def test_distancia_maximo_52s_lejos_de_maximos():
    highs = [20.0] * 251 + [10.0]
    b = _barras(closes=[10.0] * 252, highs=highs)
    assert mom.distancia_maximo_52s(b) == 0.5


def test_rsi_100_sin_perdidas():
    closes = [10.0 + i for i in range(20)]  # siempre sube
    assert mom.rsi(_barras(closes=closes)) == 100.0


def test_rsi_rango_valido_con_datos_mixtos():
    closes = [10, 11, 10.5, 11.5, 11, 12, 11.5, 12.5, 12, 13, 12.5, 13.5, 13, 14, 13.5, 14.5]
    r = mom.rsi(_barras(closes=closes), periodo=14)
    assert r is not None and 0.0 <= r <= 100.0


def test_atr_none_con_una_sola_barra():
    assert mom.atr(_barras(closes=[10.0])) is None


def test_atr_positivo_con_rango_real():
    closes = [10.0 + (i % 3) for i in range(30)]
    assert mom.atr(_barras(closes=closes)) is not None
    assert mom.atr(_barras(closes=closes)) > 0


def test_ema_none_sin_suficiente_historia():
    assert mom.ema([1.0, 2.0, 3.0], 20) is None


def test_ema_converge_a_precio_constante():
    valores = [50.0] * 60
    assert mom.ema(valores, 20) == 50.0


def test_macd_none_sin_suficiente_historia():
    assert mom.macd(_barras(closes=[10.0] * 10)) is None


def test_macd_devuelve_tres_valores_con_historia_suficiente():
    closes = [10.0 + i * 0.05 for i in range(80)]
    r = mom.macd(_barras(closes=closes))
    assert r is not None
    macd_val, señal_val, hist = r
    assert hist == macd_val - señal_val


def test_vwap_proxy_none_sin_suficiente_historia():
    assert mom.vwap_proxy(_barras(closes=[10.0] * 5), ventana=10) is None


def test_vwap_proxy_es_precio_tipico_ponderado():
    # Todos los días idénticos -> el proxy debe ser igual al precio típico de cualquiera de ellos.
    closes = [10.0] * 10
    b = _barras(closes=closes)
    tipico = (b.high[0] + b.low[0] + b.close[0]) / 3
    assert abs(mom.vwap_proxy(b, ventana=10) - tipico) < 1e-9


def test_calcular_agrega_todos_los_factores():
    closes = [10.0 + i * 0.05 for i in range(300)]
    b = _barras(closes=closes)
    f = mom.calcular(b)
    assert f.rvol is not None
    assert f.distancia_max_52s is not None
    assert f.ema20 is not None and f.ema50 is not None
    assert f.rsi is not None
    assert f.atr is not None
