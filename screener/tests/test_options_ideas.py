"""Pruebas del módulo de ideas de opciones: determinístico, nunca inventa
un dato que no tiene (IV Rank, riesgo/ganancia de estrategias de varias
patas), y nunca genera una señal de compra/venta."""

from __future__ import annotations

import re

from screener.options_ideas import (
    DatosOpciones,
    clasificar_iv,
    clasificar_tendencia,
    generar_ideas,
    movimiento_esperado,
)
from screener.scoring import Puntuacion


def _p(tendencia_cruda=None, vol_historica=None, precio=100.0):
    return Puntuacion(
        ticker="X", score_total=70, tendencia_cruda=tendencia_cruda,
        vol_historica_anual=vol_historica, precio_actual=precio,
    )


def _datos(**kwargs):
    return DatosOpciones(ticker="X", **kwargs)


# ------------------------- clasificación -------------------------

def test_clasificar_tendencia():
    assert clasificar_tendencia(3.0) == "alcista"
    assert clasificar_tendencia(2.0) == "neutral"
    assert clasificar_tendencia(1.0) == "neutral"
    assert clasificar_tendencia(0.0) == "bajista"
    assert clasificar_tendencia(None) == "desconocida"


def test_clasificar_iv_compara_contra_volatilidad_historica():
    assert clasificar_iv(0.40, 0.25) == "alta"
    assert clasificar_iv(0.15, 0.25) == "baja"


def test_clasificar_iv_sin_datos_no_adivina():
    assert clasificar_iv(None, 0.25) is None
    assert clasificar_iv(0.30, None) is None
    assert clasificar_iv(0.30, 0.0) is None


# ------------------------- generación de ideas -------------------------

def test_sin_tendencia_no_hay_ideas():
    assert generar_ideas(_p(tendencia_cruda=None), _datos()) == []


def test_iv_conocida_da_una_sola_idea():
    p = _p(tendencia_cruda=3.0, vol_historica=0.20)
    datos = _datos(iv_call_atm=0.35, iv_put_atm=0.33)  # iv_actual=0.34 > 0.20 -> alta
    ideas = generar_ideas(p, datos)
    assert len(ideas) == 1
    assert ideas[0].nombre == "Covered Call"


def test_iv_desconocida_da_las_dos_ramas():
    p = _p(tendencia_cruda=3.0, vol_historica=0.20)
    ideas = generar_ideas(p, _datos())  # sin IV -> no se puede clasificar
    assert {i.nombre for i in ideas} == {"Long Call", "Covered Call"}


def test_tendencia_bajista_sugiere_estrategias_bajistas():
    p = _p(tendencia_cruda=0.0, vol_historica=0.20)
    ideas = generar_ideas(p, _datos())
    assert {i.nombre for i in ideas} == {"Long Put", "Bear Call Spread"}


def test_nunca_es_una_senal_de_compra_o_venta():
    """El nombre de la estrategia nunca debe ser una instrucción de trade."""
    p = _p(tendencia_cruda=3.0, vol_historica=0.20)
    for iv_valor in (0.10, 0.40):
        datos = _datos(iv_call_atm=iv_valor, iv_put_atm=iv_valor)
        for idea in generar_ideas(p, datos):
            bajo = (idea.nombre + idea.razon).lower()
            assert "compra ahora" not in bajo
            assert "vende ahora" not in bajo
            assert "recomendado" not in bajo


# ------------------------- riesgo/ganancia: real vs. fórmula -------------------------

def test_long_call_con_datos_reales_calcula_numeros_exactos():
    p = _p(tendencia_cruda=3.0, vol_historica=0.20)
    datos = _datos(iv_call_atm=0.10, iv_put_atm=0.10,
                    strike_call_atm=100.0, prima_call_atm=2.5)
    ideas = generar_ideas(p, datos)
    idea = next(i for i in ideas if i.nombre == "Long Call")
    assert "$250" in idea.riesgo_maximo
    assert "$102.50" in idea.breakeven
    assert "Ilimitada" in idea.ganancia_maxima


def test_long_put_con_datos_reales_calcula_numeros_exactos():
    p = _p(tendencia_cruda=0.0, vol_historica=0.20)
    datos = _datos(iv_call_atm=0.10, iv_put_atm=0.10,
                    strike_put_atm=100.0, prima_put_atm=3.0)
    ideas = generar_ideas(p, datos)
    idea = next(i for i in ideas if i.nombre == "Long Put")
    assert "$300" in idea.riesgo_maximo
    assert "$97.00" in idea.breakeven


def test_estrategias_multi_pata_nunca_inventan_numeros():
    """Covered Call/Straddle/Iron Condor/Bear Call Spread deben explicar la
    fórmula, nunca un dólar específico -- este módulo no cotiza las
    combinaciones de strikes que esas estrategias necesitarían. "$0" (el
    piso teórico del precio de una acción) es un concepto, no un dato
    inventado, así que se permite -- lo que no se permite es un monto real
    como "$250"."""
    p = _p(tendencia_cruda=3.0, vol_historica=0.20)
    datos = _datos(iv_call_atm=0.40, iv_put_atm=0.40,
                    strike_call_atm=100.0, prima_call_atm=5.0)
    idea = next(i for i in generar_ideas(p, datos) if i.nombre == "Covered Call")
    for campo in (idea.riesgo_maximo, idea.ganancia_maxima, idea.breakeven):
        assert not re.search(r"\$[1-9]", campo), campo


def test_sin_datos_reales_long_call_usa_formula():
    p = _p(tendencia_cruda=3.0, vol_historica=0.20)
    idea = next(i for i in generar_ideas(p, _datos(iv_call_atm=0.10, iv_put_atm=0.10))
                if i.nombre == "Long Call")
    assert "$" not in idea.riesgo_maximo
    assert "depende" in idea.riesgo_maximo.lower()


# ------------------------- movimiento esperado -------------------------

def test_movimiento_esperado_sin_iv_es_honesto():
    p = _p(precio=100.0)
    assert "No disponible" in movimiento_esperado(p, _datos())


def test_movimiento_esperado_con_datos_reales():
    p = _p(precio=100.0)
    datos = _datos(iv_call_atm=0.30, iv_put_atm=0.30, dias_a_vencimiento=36,
                    vencimiento="2026-08-15")
    texto = movimiento_esperado(p, datos)
    assert "±$" in texto
    assert "2026-08-15" in texto
