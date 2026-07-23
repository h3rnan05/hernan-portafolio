"""Pruebas de las funciones públicas de screener/factors/technical.py que
telegram_bot/trade_command.py reutiliza como niveles técnicos reales
(sma, maximo_52s) para el Plan de acción de /trade -- nunca un porcentaje
o nivel de soporte inventado."""

from __future__ import annotations

from screener.data.provider import Barras
from screener.factors import technical as tech


def _barras(precios: list[float]) -> Barras:
    n = len(precios)
    return Barras("AAPL", [str(i) for i in range(n)], precios,
                  [p * 1.01 for p in precios], [p * 0.99 for p in precios], [1e7] * n)


def test_sma_promedia_las_ultimas_n_barras():
    assert tech.sma([1.0, 2.0, 3.0, 4.0, 5.0], 3) == 4.0


def test_sma_insuficientes_barras_da_none():
    assert tech.sma([1.0, 2.0], 5) is None


def test_maximo_52s_usa_las_ultimas_252_barras():
    precios = [100.0] * 300
    precios[-260] = 500.0  # fuera de la ventana de 252 -- no debe contar
    precios[-100] = 200.0  # dentro de la ventana -- sí debe contar
    b = _barras(precios)
    assert tech.maximo_52s(b) == 200.0


def test_maximo_52s_con_menos_de_252_barras_usa_todas():
    b = _barras([10.0, 30.0, 20.0])
    assert tech.maximo_52s(b) == 30.0


def test_maximo_52s_lista_vacia_da_none():
    b = _barras([])
    assert tech.maximo_52s(b) is None


def test_proximidad_maximo_52s_en_maximos_da_uno():
    b = _barras([90.0, 95.0, 100.0])
    assert tech.proximidad_maximo_52s(b) == 1.0


def test_proximidad_maximo_52s_reutiliza_maximo_52s():
    b = _barras([100.0, 50.0])
    assert tech.proximidad_maximo_52s(b) == 0.5
