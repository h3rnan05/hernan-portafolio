"""Pruebas de los factores intradía -- sin red. Construye velas con
timestamps ISO reales para poder verificar premarket/sesión regular,
VWAP real, EMA9, RVOL inmediato y aceleración de volumen a mano."""

from __future__ import annotations

from momentum_hunter.factors import intradia as fi
from momentum_hunter.models import BarraIntradia


def _marca(hora: str) -> str:
    return f"2026-07-26T{hora}:00+00:00"


def _bi_con_premarket_y_regular() -> BarraIntradia:
    # 13:00-13:29 UTC = premarket. 13:30 en adelante = sesión regular.
    marcas = [_marca("13:00"), _marca("13:15"), _marca("13:29"), _marca("13:30"), _marca("13:31"), _marca("13:32")]
    closes = [4.00, 4.10, 4.20, 4.30, 4.40, 4.50]
    highs = [4.05, 4.15, 4.25, 4.35, 4.45, 4.55]
    lows = [3.95, 4.05, 4.15, 4.25, 4.35, 4.45]
    vols = [1_000, 2_000, 3_000, 5_000, 6_000, 7_000]
    return BarraIntradia("TST", marcas, closes, closes, highs, lows, vols)


def test_es_premarket_y_es_sesion_regular():
    assert fi.es_premarket(_marca("13:00")) is True
    assert fi.es_premarket(_marca("13:29")) is True
    assert fi.es_sesion_regular(_marca("13:30")) is True
    assert fi.es_sesion_regular(_marca("13:00")) is False


def test_barras_de_hoy_filtra_por_fecha_de_la_ultima_vela():
    marcas = ["2026-07-24T13:30:00+00:00", "2026-07-25T13:30:00+00:00", "2026-07-26T13:30:00+00:00"]
    closes = [1.0, 2.0, 3.0]
    bi = BarraIntradia("TST", marcas, closes, closes, closes, closes, [100.0] * 3)
    hoy = fi.barras_de_hoy(bi)
    assert hoy.close == [3.0]


def test_maximo_premarket_solo_considera_velas_de_premarket():
    bi = _bi_con_premarket_y_regular()
    assert fi.maximo_premarket(bi) == 4.25  # high de la vela de 13:29


def test_maximo_dia_incluye_todo():
    bi = _bi_con_premarket_y_regular()
    assert fi.maximo_dia(bi) == 4.55


def test_rango_apertura_solo_los_primeros_minutos_de_sesion_regular():
    bi = _bi_con_premarket_y_regular()
    rango = fi.rango_apertura(bi, minutos=2)
    assert rango is not None
    alto, bajo = rango
    # Solo 13:30 y 13:31 caen dentro de los primeros 2 minutos de sesión regular.
    assert alto == 4.45
    assert bajo == 4.25


def test_vwap_real_excluye_premarket():
    bi = _bi_con_premarket_y_regular()
    vwap = fi.vwap_real(bi)
    assert vwap is not None
    # Debe estar dentro del rango de precios de la sesión REGULAR (4.25-4.55),
    # nunca influenciado por el premarket (3.95-4.25).
    assert 4.25 <= vwap <= 4.55


def test_ema9_none_sin_suficiente_historia():
    bi = _bi_con_premarket_y_regular()  # solo 6 velas
    assert fi.ema9_intradia(bi) is None


def test_ema9_converge_a_precio_constante():
    marcas = [_marca(f"13:{30+i:02d}") for i in range(12)]
    closes = [2.0] * 12
    bi = BarraIntradia("TST", marcas, closes, closes, closes, closes, [1_000.0] * 12)
    assert fi.ema9_intradia(bi) == 2.0


def test_rvol_actual_compara_contra_las_anteriores():
    vols = [1_000.0] * 5 + [5_000.0]
    marcas = [_marca(f"13:{30+i:02d}") for i in range(6)]
    closes = [1.0] * 6
    bi = BarraIntradia("TST", marcas, closes, closes, closes, closes, vols)
    assert fi.rvol_actual(bi, ventana=5) == 5.0


def test_aceleracion_volumen_mayor_a_uno_si_esta_acelerando():
    vols = [1_000.0, 1_000.0, 1_000.0, 3_000.0, 3_000.0, 3_000.0]
    marcas = [_marca(f"13:{30+i:02d}") for i in range(6)]
    closes = [1.0] * 6
    bi = BarraIntradia("TST", marcas, closes, closes, closes, closes, vols)
    assert fi.aceleracion_volumen(bi, ventana=3) == 3.0


def test_gap_pct_usa_apertura_regular_no_premarket():
    bi = _bi_con_premarket_y_regular()
    # Apertura regular (13:30) = 4.30 (open==close en el fixture). Cierre de ayer = 4.00.
    gap = fi.gap_pct(bi, cierre_anterior=4.00)
    assert abs(gap - 0.075) < 1e-9


def test_velas_desde_ruptura_cuenta_desde_la_primera_vela_sobre_el_nivel():
    closes = [4.0, 4.6, 4.7, 4.8]  # rompe 4.5 en la vela índice 1, se mantiene arriba
    marcas = [_marca(f"13:{30+i:02d}") for i in range(4)]
    bi = BarraIntradia("TST", marcas, closes, closes, closes, closes, [1_000.0] * 4)
    assert fi.velas_desde_ruptura(bi, nivel=4.5) == 2


def test_velas_desde_ruptura_none_si_no_esta_por_encima():
    closes = [4.0, 4.1]
    marcas = [_marca("13:30"), _marca("13:31")]
    bi = BarraIntradia("TST", marcas, closes, closes, closes, closes, [1_000.0, 1_000.0])
    assert fi.velas_desde_ruptura(bi, nivel=5.0) is None


def test_calcular_agrega_todos_los_factores():
    bi = _bi_con_premarket_y_regular()
    f = fi.calcular(bi, cierre_anterior=4.00)
    assert f.precio_actual == 4.50
    assert f.maximo_premarket == 4.25
    assert f.maximo_dia == 4.55
    assert f.gap_pct is not None


def test_macd_intradia_none_sin_suficiente_historia():
    bi = _bi_con_premarket_y_regular()  # solo 6 velas, MACD(12,26,9) necesita muchas más
    assert fi.macd_intradia(bi) is None


def test_macd_intradia_positivo_en_tendencia_alcista_sostenida():
    # Suficientes velas para las tres EMAs del MACD (12/26/9): una
    # tendencia alcista sostenida y sin ruido debe dar línea > señal
    # (momentum a favor, mismo criterio que el MACD diario de
    # `factors/momentum.py`).
    n = 60
    marcas = [_marca(f"{13 + (30 + i) // 60:02d}:{(30 + i) % 60:02d}") for i in range(n)]
    closes = [1.0 + i * 0.01 for i in range(n)]
    bi = BarraIntradia("TST", marcas, closes, closes, closes, closes, [1_000.0] * n)
    resultado = fi.macd_intradia(bi)
    assert resultado is not None
    linea, señal = resultado
    assert linea > señal


def test_calcular_incluye_macd_cuando_hay_suficiente_historia():
    n = 60
    marcas = [_marca(f"{13 + (30 + i) // 60:02d}:{(30 + i) % 60:02d}") for i in range(n)]
    closes = [1.0 + i * 0.01 for i in range(n)]
    bi = BarraIntradia("TST", marcas, closes, closes, closes, closes, [1_000.0] * n)
    f = fi.calcular(bi)
    assert f.macd is not None
    assert f.macd_signal is not None
