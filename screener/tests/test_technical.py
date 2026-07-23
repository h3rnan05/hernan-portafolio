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


def test_atr_calcula_true_range_promedio():
    # high=p*1.01, low=p*0.99 (rango constante del 2%) -- con precio
    # constante, true range = high - low = 2.0 en cada barra.
    b = _barras([100.0] * 5)
    assert tech.atr(b, periodo=20) == 2.0


def test_atr_usa_solo_la_ventana_pedida():
    # Un salto de precio grande fuera de la ventana no debe afectar el ATR.
    precios = [100.0] * 30
    precios[0] = 500.0  # salto grande, pero fuera de la ventana de 5
    b = _barras(precios)
    assert tech.atr(b, periodo=5) == 2.0


def test_atr_menos_de_dos_barras_da_none():
    assert tech.atr(_barras([100.0])) is None
    assert tech.atr(_barras([])) is None


def test_niveles_precio_entrada_es_spot_menos_1atr():
    niveles = tech.niveles_precio(spot=100.0, atr_val=5.0, sma50=None)
    assert niveles["entrada"] == 95.0
    assert niveles["ideal"] == 95.0  # sin sma50, ideal cae al mismo valor de entrada
    assert niveles["cancelar"] == 85.0  # ideal - 2xATR


def test_niveles_precio_ideal_es_el_mas_profundo_entre_sma50_y_entrada():
    niveles = tech.niveles_precio(spot=100.0, atr_val=2.0, sma50=90.0)
    assert niveles["entrada"] == 98.0
    assert niveles["ideal"] == 90.0
    assert niveles["cancelar"] == 86.0  # 90 - 2*2


def test_niveles_precio_sma50_por_encima_de_entrada_usa_entrada():
    # "ideal" nunca debe quedar por ENCIMA de "entrada".
    niveles = tech.niveles_precio(spot=100.0, atr_val=2.0, sma50=99.0)
    assert niveles["ideal"] == 98.0
    assert niveles["ideal"] <= niveles["entrada"]


def test_niveles_precio_sin_atr_da_todo_none():
    niveles = tech.niveles_precio(spot=100.0, atr_val=None, sma50=90.0)
    assert niveles == {"entrada": None, "ideal": 90.0, "cancelar": None}
