"""Pruebas del motor determinístico de opciones: Black-Scholes, Greeks,
probabilidad y valor esperado. Se validan contra identidades matemáticas
conocidas (paridad put-call, delta en su rango teórico, el valor esperado
del precio final bajo la medida risk-neutral es el precio forward) --no
solo "corre sin errores", sino que los números son correctos."""

from __future__ import annotations

import math

from screener.options_math import (
    Greeks,
    greeks,
    precio_black_scholes,
    probabilidad_sobre,
    valor_esperado_payoff,
)

SPOT, STRIKE, DIAS, IV, TASA = 100.0, 100.0, 30, 0.30, 0.045


def test_precio_black_scholes_paridad_put_call():
    call = precio_black_scholes(SPOT, STRIKE, DIAS, IV, "call", TASA)
    put = precio_black_scholes(SPOT, STRIKE, DIAS, IV, "put", TASA)
    anios = DIAS / 365.0
    esperado = SPOT - STRIKE * math.exp(-TASA * anios)
    assert abs((call - put) - esperado) < 1e-6


def test_precio_black_scholes_entradas_invalidas_da_none():
    assert precio_black_scholes(0, STRIKE, DIAS, IV, "call") is None
    assert precio_black_scholes(SPOT, STRIKE, 0, IV, "call") is None
    assert precio_black_scholes(SPOT, STRIKE, DIAS, 0, "call") is None
    assert precio_black_scholes(SPOT, -10, DIAS, IV, "put") is None


def test_precio_call_mas_caro_cuanto_mas_itm():
    barata = precio_black_scholes(SPOT, 120, DIAS, IV, "call", TASA)
    cara = precio_black_scholes(SPOT, 80, DIAS, IV, "call", TASA)
    assert cara > barata > 0


def test_greeks_delta_en_su_rango_teorico():
    g_call = greeks(SPOT, STRIKE, DIAS, IV, "call", TASA)
    g_put = greeks(SPOT, STRIKE, DIAS, IV, "put", TASA)
    assert 0.0 < g_call.delta < 1.0
    assert -1.0 < g_put.delta < 0.0
    # put-call: delta_call - delta_put == 1 (identidad de paridad)
    assert abs((g_call.delta - g_put.delta) - 1.0) < 1e-9


def test_greeks_gamma_y_vega_iguales_para_call_y_put_mismo_strike():
    g_call = greeks(SPOT, STRIKE, DIAS, IV, "call", TASA)
    g_put = greeks(SPOT, STRIKE, DIAS, IV, "put", TASA)
    assert abs(g_call.gamma - g_put.gamma) < 1e-9
    assert abs(g_call.vega - g_put.vega) < 1e-9
    assert g_call.gamma > 0
    assert g_call.vega > 0


def test_greeks_delta_call_crece_con_precio_mas_alto_del_strike():
    g_otm = greeks(SPOT, 120, DIAS, IV, "call", TASA)
    g_itm = greeks(SPOT, 80, DIAS, IV, "call", TASA)
    assert g_itm.delta > g_otm.delta


def test_greeks_entradas_invalidas_da_none():
    assert greeks(SPOT, STRIKE, 0, IV, "call") is None
    assert isinstance(greeks(SPOT, STRIKE, DIAS, IV, "call"), Greeks)


def test_probabilidad_sobre_decrece_con_umbral_mas_alto():
    p_bajo = probabilidad_sobre(SPOT, 90, DIAS, IV, TASA)
    p_alto = probabilidad_sobre(SPOT, 110, DIAS, IV, TASA)
    assert 0.0 < p_alto < p_bajo < 1.0


def test_probabilidad_sobre_atm_cerca_de_la_mitad():
    p = probabilidad_sobre(SPOT, STRIKE, DIAS, IV, TASA)
    assert 0.3 < p < 0.7  # ATM: ni casi seguro ni casi imposible


def test_probabilidad_sobre_entradas_invalidas_da_none():
    assert probabilidad_sobre(SPOT, STRIKE, 0, IV) is None


def test_valor_esperado_payoff_identidad_da_precio_forward():
    # E[S_T] bajo la medida risk-neutral es exactamente el precio forward
    # S0 * e^(rT) -- es la identidad que define la construcción lognormal
    # usada. Si esto no cuadra, la integración numérica está mal.
    ev = valor_esperado_payoff(SPOT, DIAS, IV, lambda s: s, TASA)
    forward = SPOT * math.exp(TASA * DIAS / 365.0)
    assert abs(ev - forward) / forward < 1e-3


def test_valor_esperado_payoff_call_coincide_con_black_scholes_descontado():
    # BS_price = e^(-rT) * E[payoff] -> E[payoff] = BS_price * e^(rT)
    payoff_call = lambda s: max(s - STRIKE, 0.0)  # noqa: E731
    ev = valor_esperado_payoff(SPOT, DIAS, IV, payoff_call, TASA)
    precio = precio_black_scholes(SPOT, STRIKE, DIAS, IV, "call", TASA)
    esperado = precio * math.exp(TASA * DIAS / 365.0)
    assert abs(ev - esperado) / esperado < 5e-3


def test_valor_esperado_payoff_entradas_invalidas_da_none():
    assert valor_esperado_payoff(SPOT, 0, IV, lambda s: s) is None
