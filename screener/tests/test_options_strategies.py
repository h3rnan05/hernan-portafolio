"""Pruebas del motor de construcción y ranking de estrategias de opciones.
Usa una cadena sintética generada con Black-Scholes (screener.options_math)
para que las primas sean internamente consistentes -- así se puede validar
que los breakevens/riesgo/ganancia se derivan correctamente de strikes y
primas reales, no solo que "no truena"."""

from __future__ import annotations

import math

from screener.options_ideas import CadenaOpciones, ContratoOpcion
from screener.options_math import precio_black_scholes, probabilidad_sobre, valor_esperado_payoff
from screener.options_strategies import (
    DELTA_SPREAD_VERTICAL,
    EstrategiaOpciones,
    _bear_call_spread,
    _bear_put_spread,
    _bull_call_spread,
    _bull_put_spread,
    _cash_secured_put,
    _covered_call,
    _ev_por_riesgo,
    _interpolar_iv,
    _iron_condor,
    _iv_local,
    _liquidez,
    _liquidez_contrato,
    _long_call,
    _long_put,
    _payoff_posicion,
    _percentil,
    construir_estrategias,
    iv_en_precio,
    iv_referencia,
    rankear,
)

SPOT, DIAS, IV = 100.0, 35, 0.30


def _cadena_sintetica(spot=SPOT, dias=DIAS, iv=IV) -> CadenaOpciones:
    calls, puts = [], []
    for k in range(60, 145, 5):
        precio_call = precio_black_scholes(spot, k, dias, iv, "call")
        precio_put = precio_black_scholes(spot, k, dias, iv, "put")
        oi = max(50, int(600 - abs(k - spot) * 10))
        spread_c = max(0.05, precio_call * 0.05)
        spread_p = max(0.05, precio_put * 0.05)
        calls.append(ContratoOpcion(
            strike=float(k), prima=round(precio_call, 2),
            bid=round(precio_call - spread_c / 2, 2), ask=round(precio_call + spread_c / 2, 2),
            iv=iv, open_interest=oi, volumen=oi // 2))
        puts.append(ContratoOpcion(
            strike=float(k), prima=round(precio_put, 2),
            bid=round(precio_put - spread_p / 2, 2), ask=round(precio_put + spread_p / 2, 2),
            iv=iv, open_interest=oi, volumen=oi // 2))
    return CadenaOpciones(ticker="TEST", vencimiento="2099-01-01", dias_a_vencimiento=dias, calls=calls, puts=puts)


def _sanidad(e) -> None:
    assert e.riesgo_maximo is not None and e.riesgo_maximo > 0
    if e.ganancia_maxima is not None:
        assert e.ganancia_maxima > 0
    assert e.breakevens
    if e.probabilidad_exito is not None:
        assert 0.0 <= e.probabilidad_exito <= 1.0
    assert e.valor_esperado is not None and math.isfinite(e.valor_esperado)


def test_long_call_estrategia_razonable():
    cadena = _cadena_sintetica()
    e = _long_call(cadena, SPOT, DIAS, IV)
    assert e is not None
    _sanidad(e)
    assert e.ganancia_maxima is None  # ilimitada
    assert e.patas[0].accion == "comprar"


def test_long_put_estrategia_razonable():
    cadena = _cadena_sintetica()
    e = _long_put(cadena, SPOT, DIAS, IV)
    assert e is not None
    _sanidad(e)
    assert e.patas[0].accion == "comprar"


def test_bull_call_spread_orden_de_strikes_correcto():
    cadena = _cadena_sintetica()
    e = _bull_call_spread(cadena, SPOT, DIAS, IV)
    assert e is not None
    _sanidad(e)
    largo, corto = e.patas
    assert largo.accion == "comprar" and corto.accion == "vender"
    assert corto.strike > largo.strike


def test_bear_put_spread_orden_de_strikes_correcto():
    cadena = _cadena_sintetica()
    e = _bear_put_spread(cadena, SPOT, DIAS, IV)
    assert e is not None
    _sanidad(e)
    largo, corto = e.patas
    assert largo.accion == "comprar" and corto.accion == "vender"
    assert corto.strike < largo.strike


def test_bull_put_spread_es_credito():
    cadena = _cadena_sintetica()
    e = _bull_put_spread(cadena, SPOT, DIAS, IV)
    assert e is not None
    _sanidad(e)
    corto, largo = e.patas
    assert corto.accion == "vender" and largo.accion == "comprar"
    assert corto.strike > largo.strike
    assert e.ganancia_maxima == round(e.ganancia_maxima, 6)  # crédito positivo, no None


def test_bear_call_spread_es_credito():
    cadena = _cadena_sintetica()
    e = _bear_call_spread(cadena, SPOT, DIAS, IV)
    assert e is not None
    _sanidad(e)
    corto, largo = e.patas
    assert corto.accion == "vender" and largo.accion == "comprar"
    assert corto.strike < largo.strike


def test_covered_call_requiere_capital_adicional():
    cadena = _cadena_sintetica()
    e = _covered_call(cadena, SPOT, DIAS, IV)
    assert e is not None
    _sanidad(e)
    assert e.capital_adicional_requerido is True
    assert e.patas[0].accion == "vender"


def test_cash_secured_put_requiere_capital_adicional():
    cadena = _cadena_sintetica()
    e = _cash_secured_put(cadena, SPOT, DIAS, IV)
    assert e is not None
    _sanidad(e)
    assert e.capital_adicional_requerido is True


def test_iron_condor_cuatro_patas_dos_breakevens():
    cadena = _cadena_sintetica()
    e = _iron_condor(cadena, SPOT, DIAS, IV)
    assert e is not None
    _sanidad(e)
    assert len(e.patas) == 4
    assert len(e.breakevens) == 2
    assert e.breakevens[0] < SPOT < e.breakevens[1]


def test_construir_estrategias_arma_multiples_sin_calendar_spread():
    cadena = _cadena_sintetica()
    estrategias = construir_estrategias(cadena, SPOT)
    nombres = {e.nombre for e in estrategias}
    assert len(estrategias) >= 7
    assert "Calendar Spread" not in nombres  # fuera de alcance, documentado
    for e in estrategias:
        _sanidad(e)


def test_construir_estrategias_cadena_vacia_no_rompe():
    vacia = CadenaOpciones(ticker="ZZZZ", vencimiento="2099-01-01", dias_a_vencimiento=35, calls=[], puts=[])
    assert construir_estrategias(vacia, SPOT) == []


def test_iv_referencia_promedia_atm_call_y_put():
    cadena = _cadena_sintetica()
    iv = iv_referencia(cadena, SPOT)
    assert iv is not None
    assert abs(iv - IV) < 1e-9  # cadena sintética construida con IV constante


def test_iv_referencia_cadena_vacia_da_none():
    vacia = CadenaOpciones(ticker="ZZZZ", vencimiento="2099-01-01", dias_a_vencimiento=35, calls=[], puts=[])
    assert iv_referencia(vacia, SPOT) is None


def test_construir_estrategias_sin_dias_no_rompe():
    cadena = _cadena_sintetica()
    sin_dias = CadenaOpciones(ticker="TEST", vencimiento="2099-01-01", dias_a_vencimiento=0,
                              calls=cadena.calls, puts=cadena.puts)
    assert construir_estrategias(sin_dias, SPOT) == []


def test_rankear_devuelve_mismo_conjunto_reordenado():
    cadena = _cadena_sintetica()
    estrategias = construir_estrategias(cadena, SPOT)
    ranking = rankear(estrategias)
    assert {e.nombre for e in ranking} == {e.nombre for e in estrategias}
    assert len(ranking) == len(estrategias)


def test_rankear_lista_vacia_no_rompe():
    assert rankear([]) == []


def _estrategia_minima(nombre, ev, riesgo, prob, liquidez) -> EstrategiaOpciones:
    return EstrategiaOpciones(
        nombre=nombre, patas=[], razon="", riesgo_maximo=riesgo, ganancia_maxima=None,
        breakevens=[100.0], probabilidad_exito=prob, valor_esperado=ev, liquidez_score=liquidez,
    )


def test_rankear_no_deja_que_probabilidad_domine_un_ev_por_riesgo_claramente_mejor():
    # Regresión: la primera versión sumaba EV/riesgo (una fracción típica
    # de ~0.1%-1%) directamente con probabilidad/liquidez (0-1) usando
    # pesos fijos -- probabilidad y liquidez dominaban el score sin que
    # el peso de 50% de EV/riesgo tuviera ningún efecto real. Con
    # percentiles, una estrategia con EV/riesgo muchísimo mejor (aunque
    # con probabilidad/liquidez apenas menores) debe poder ganar.
    barata_alta_ev = _estrategia_minima("A", ev=50.0, riesgo=500.0, prob=0.60, liquidez=80.0)  # ev/riesgo=10%
    cara_baja_ev = _estrategia_minima("B", ev=-3.0, riesgo=9000.0, prob=0.77, liquidez=88.0)   # ev/riesgo=-0.03%
    ranking = rankear([barata_alta_ev, cara_baja_ev])
    assert ranking[0].nombre == "A"


def test_percentil_de_un_solo_valor_es_uno():
    assert _percentil([5.0], 5.0) == 1.0


def test_percentil_ordena_correctamente():
    valores = [1.0, 2.0, 3.0]
    assert _percentil(valores, 1.0) < _percentil(valores, 2.0) < _percentil(valores, 3.0)


def test_ev_por_riesgo_sin_riesgo_valido_da_cero():
    e = _estrategia_minima("X", ev=10.0, riesgo=0.0, prob=0.5, liquidez=50.0)
    assert _ev_por_riesgo(e) == 0.0


def test_liquidez_contrato_sin_datos_da_none():
    vacio = ContratoOpcion(strike=100.0, prima=None, bid=None, ask=None, iv=None,
                           open_interest=None, volumen=None)
    assert _liquidez_contrato(vacio) is None
    assert _liquidez([vacio]) is None


def test_liquidez_promedia_varias_patas():
    alta = ContratoOpcion(strike=100.0, prima=5.0, bid=4.95, ask=5.05, iv=0.30,
                          open_interest=1000, volumen=500)
    baja = ContratoOpcion(strike=110.0, prima=1.0, bid=0.5, ask=1.5, iv=0.30,
                          open_interest=10, volumen=5)
    score_alta = _liquidez_contrato(alta)
    score_baja = _liquidez_contrato(baja)
    assert score_alta > score_baja
    promedio = _liquidez([alta, baja])
    assert min(score_alta, score_baja) < promedio < max(score_alta, score_baja)


def test_delta_spread_vertical_es_una_convencion_documentada():
    # No es un test de comportamiento -- solo fija el contrato de la
    # convención usada (0.30) para que un cambio accidental del valor no
    # pase desapercibido.
    assert DELTA_SPREAD_VERTICAL == 0.30


# ------------------------- superficie de volatilidad (skew) -------------------------

def _cadena_con_skew(spot=SPOT, dias=DIAS, iv_base=IV, pendiente=0.002) -> CadenaOpciones:
    """Cadena sintética con skew de puts hacia abajo (strikes más bajos =
    IV más alta, la convención empírica estándar de equities -- el mismo
    patrón que se observó en datos reales de AAPL durante la auditoría).
    Las primas de cada contrato son Black-Scholes consistentes CON su
    propio IV real por strike, no con un IV plano."""
    calls, puts = [], []
    for k in range(60, 145, 5):
        iv_put = iv_base + pendiente * max(0.0, spot - k)
        iv_call = iv_base + pendiente * max(0.0, k - spot) * 0.3
        precio_call = precio_black_scholes(spot, k, dias, iv_call, "call")
        precio_put = precio_black_scholes(spot, k, dias, iv_put, "put")
        oi = max(50, int(600 - abs(k - spot) * 10))
        calls.append(ContratoOpcion(
            strike=float(k), prima=round(precio_call, 2),
            bid=round(precio_call * 0.975, 2), ask=round(precio_call * 1.025, 2),
            iv=iv_call, open_interest=oi, volumen=oi // 2))
        puts.append(ContratoOpcion(
            strike=float(k), prima=round(precio_put, 2),
            bid=round(precio_put * 0.975, 2), ask=round(precio_put * 1.025, 2),
            iv=iv_put, open_interest=oi, volumen=oi // 2))
    return CadenaOpciones(ticker="TEST", vencimiento="2099-01-01", dias_a_vencimiento=dias, calls=calls, puts=puts)


def test_interpolar_iv_interpola_linealmente_entre_dos_strikes():
    puntos = [(90.0, 0.30), (100.0, 0.40)]
    assert _interpolar_iv(puntos, 95.0) == 0.35  # punto medio exacto


def test_interpolar_iv_fuera_de_rango_usa_extremo_mas_cercano():
    puntos = [(90.0, 0.30), (100.0, 0.40)]
    assert _interpolar_iv(puntos, 50.0) == 0.30  # nunca extrapola
    assert _interpolar_iv(puntos, 150.0) == 0.40


def test_interpolar_iv_sin_puntos_validos_da_none():
    assert _interpolar_iv([], 100.0) is None
    assert _interpolar_iv([(90.0, None), (100.0, None)], 95.0) is None


def test_iv_en_precio_usa_puts_bajo_spot_y_calls_sobre_spot():
    cadena = _cadena_con_skew()
    iv_abajo = iv_en_precio(cadena, 85.0, SPOT)
    iv_arriba = iv_en_precio(cadena, 115.0, SPOT)
    # con skew hacia abajo, un precio bien por debajo del spot debe traer
    # una IV mayor que uno igual de lejos por encima
    assert iv_abajo > iv_arriba


def test_iv_local_promedia_breakevens_dados():
    cadena = _cadena_con_skew()
    iv_un_breakeven = _iv_local(cadena, SPOT, [85.0], IV)
    iv_dos_breakevens = _iv_local(cadena, SPOT, [85.0, 115.0], IV)
    esperado = (iv_en_precio(cadena, 85.0, SPOT) + iv_en_precio(cadena, 115.0, SPOT)) / 2
    assert iv_un_breakeven == iv_en_precio(cadena, 85.0, SPOT)
    assert abs(iv_dos_breakevens - esperado) < 1e-9


def test_iv_local_sin_datos_cae_al_fallback():
    vacia = CadenaOpciones(ticker="ZZZZ", vencimiento="2099-01-01", dias_a_vencimiento=35, calls=[], puts=[])
    assert _iv_local(vacia, SPOT, [90.0], 0.42) == 0.42


def test_iv_local_en_cadena_sin_skew_coincide_con_iv_plana():
    # Sanity check: si TODOS los strikes cotizan la misma IV (sin skew),
    # la IV local en cualquier breakeven debe coincidir con la IV plana
    # -- el fix no debe cambiar nada cuando no hay skew que corregir.
    cadena = _cadena_sintetica()
    plana = iv_referencia(cadena, SPOT)
    assert abs(_iv_local(cadena, SPOT, [85.0, 120.0], plana) - plana) < 1e-9


def test_probabilidad_y_ev_usan_iv_local_no_la_plana_cuando_hay_skew():
    # Regresión del hallazgo de auditoría (AAPL, Bear Put Spread #1 con
    # tesis alcista): antes, probabilidad_exito y valor_esperado de TODAS
    # las estrategias se calculaban con una única IV plana (ATM) aunque
    # las primas reales sí reflejaran el skew real por strike. Con una
    # cadena con skew real, el EV/probabilidad de una estrategia cuyo
    # breakeven cae lejos del ATM (donde el skew es más pronunciado) debe
    # diferir de lo que habría dado la IV plana -- si coincidieran
    # exactamente, el fix no estaría teniendo ningún efecto.
    cadena = _cadena_con_skew()
    e = _bear_put_spread(cadena, SPOT, DIAS, IV)
    assert e is not None

    iv_plana = iv_referencia(cadena, SPOT)
    ev_con_iv_plana = valor_esperado_payoff(SPOT, DIAS, iv_plana, lambda s: _payoff_posicion(e.patas, s))
    prob_con_iv_plana = probabilidad_sobre(SPOT, e.breakevens[0], DIAS, iv_plana)

    # El breakeven de este Bear Put Spread cae del lado de las puts, cuyo
    # skew es más pronunciado en esta cadena sintética -- el EV/prob
    # calculados con la IV local real deben diferir medibles de los que
    # habría dado la IV plana.
    assert abs(e.valor_esperado - ev_con_iv_plana) > 0.01
    assert abs(e.probabilidad_exito - (1.0 - prob_con_iv_plana)) > 1e-4
