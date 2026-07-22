"""Pruebas de /trade TICKER: red (Yahoo) mockeada por completo -- verifica
que la versión de bolsillo muestre solo lo que de verdad se pudo calcular
(nunca invente Convicción/Objetivo), que "Convicción" sea siempre un
score real (screener o ranking de estrategias, nunca una probabilidad de
éxito), y que el bloque "¿Por qué?" sea 100% determinístico (sin LLM)."""

from __future__ import annotations

import pytest

import trade_command as tc
from screener.data.provider import Barras, Fundamentales
from screener.options_ideas import CadenaOpciones, ContratoOpcion
from screener.options_math import precio_black_scholes
from screener.options_strategies import EstrategiaOpciones, PataOpcion

SPOT, DIAS, IV = 100.0, 35, 0.30


def _barras(ticker="AAPL", n=260, precio_final=SPOT):
    precios = [precio_final - (n - i) * 0.1 for i in range(n)]
    return Barras(ticker, [str(i) for i in range(n)], precios,
                  [p * 1.01 for p in precios], [p * 0.99 for p in precios],
                  [1e7] * n)


class _FakeProvider:
    def __init__(self, barras_por_ticker, fund_por_ticker):
        self._b, self._f = barras_por_ticker, fund_por_ticker

    def barras(self, tickers, dias=400):
        return {t: self._b[t] for t in tickers if t in self._b}

    def fundamentales(self, tickers):
        return {t: self._f.get(t, Fundamentales(t)) for t in tickers}


def _cadena_sintetica(ticker="AAPL", spot=SPOT, dias=DIAS, iv=IV) -> CadenaOpciones:
    calls, puts = [], []
    for k in range(60, 145, 5):
        precio_call = precio_black_scholes(spot, k, dias, iv, "call")
        precio_put = precio_black_scholes(spot, k, dias, iv, "put")
        oi = max(50, int(600 - abs(k - spot) * 10))
        calls.append(ContratoOpcion(
            strike=float(k), prima=round(precio_call, 2),
            bid=round(precio_call * 0.975, 2), ask=round(precio_call * 1.025, 2),
            iv=iv, open_interest=oi, volumen=oi // 2))
        puts.append(ContratoOpcion(
            strike=float(k), prima=round(precio_put, 2),
            bid=round(precio_put * 0.975, 2), ask=round(precio_put * 1.025, 2),
            iv=iv, open_interest=oi, volumen=oi // 2))
    return CadenaOpciones(ticker=ticker, vencimiento="2099-01-01", dias_a_vencimiento=dias,
                          calls=calls, puts=puts)


def _mock_red_basica(monkeypatch, fund=None):
    monkeypatch.setattr(tc, "YahooProvider",
                        lambda: _FakeProvider({"AAPL": _barras()}, {"AAPL": fund or Fundamentales("AAPL", nombre="Apple Inc.")}))
    monkeypatch.setattr(tc, "obtener_cadena", lambda ticker: _cadena_sintetica(ticker))
    monkeypatch.setattr(tc, "_shortlist_entry", lambda ticker: None)


def test_ticker_sin_datos_de_precio_da_mensaje_claro(monkeypatch):
    monkeypatch.setattr(tc, "YahooProvider", lambda: _FakeProvider({}, {}))
    texto = tc.generar_trade("ZZZZ")
    assert "no pude obtener datos de precio" in texto.lower()


def test_mensaje_basico_incluye_secciones_clave(monkeypatch):
    _mock_red_basica(monkeypatch)
    texto = tc.generar_trade("AAPL")
    assert "📊 AAPL — Apple Inc." in texto
    assert "Convicción del modelo:" in texto
    assert "📌 Mi conclusión" in texto
    assert "🎯" in texto  # "Mejor estrategia" o la variante "Si decidieras operar hoy..."
    assert "¿Por qué?" in texto
    assert "💰 Ejemplo" in texto
    assert "Costo máximo:" in texto
    assert "Ganancia máxima:" in texto
    assert "Punto de equilibrio:" in texto
    assert "📈 Escenarios" in texto
    assert "⚠️ Riesgos" in texto
    assert "Próximo paso" in texto
    assert "/report AAPL" in texto
    assert "/options AAPL --full" in texto
    assert "Esto no es una recomendación de compra/venta." in texto


def test_convic_no_disponible_si_no_esta_en_shortlist(monkeypatch):
    _mock_red_basica(monkeypatch)
    texto = tc.generar_trade("AAPL")
    assert "⚪ Convicción del modelo: No disponible (no está en la shortlist de hoy)" in texto


def test_convic_usa_score_real_del_screener(monkeypatch):
    _mock_red_basica(monkeypatch)
    monkeypatch.setattr(tc, "_shortlist_entry", lambda ticker: {
        "score": 84.0, "sector": "Technology", "sub_scores": {"calidad": 70.0, "valor": 50.0},
    })
    texto = tc.generar_trade("AAPL")
    assert "🟢 Convicción del modelo: 84/100" in texto


def test_cadena_none_no_rompe_y_omite_estrategias(monkeypatch):
    _mock_red_basica(monkeypatch)
    monkeypatch.setattr(tc, "obtener_cadena", lambda ticker: None)
    texto = tc.generar_trade("AAPL")
    assert "No pude calcular estrategias de opciones para hoy" in texto
    assert "💰 Ejemplo" not in texto
    assert "📈 Escenarios" not in texto
    assert "No hay estrategias de opciones disponibles hoy" in texto


def test_escenario_favorable_usa_emoji_por_signo_no_por_direccion_asumida(monkeypatch):
    """Regresión de extremo a extremo: una Covered Call (delta neto
    positivo, favorecida por una subida) con un precio objetivo de
    analistas por DEBAJO del spot debe mostrar 🔴 en ese escenario (el
    payoff real ahí es una pérdida), no 🟢 solo porque la estrategia en
    general se beneficia de que el precio suba."""
    spot = 327.74
    monkeypatch.setattr(tc, "YahooProvider", lambda: _FakeProvider(
        {"AAPL": _barras(precio_final=spot)},
        {"AAPL": Fundamentales("AAPL", nombre="Apple Inc.", analista_precio_objetivo=318.25)}))
    monkeypatch.setattr(tc, "obtener_cadena", lambda ticker: _cadena_sintetica(spot=spot))
    monkeypatch.setattr(tc, "_shortlist_entry", lambda ticker: None)
    covered_call = EstrategiaOpciones(
        nombre="Covered Call",
        patas=[PataOpcion("call", "vender", 345.0, 5.0)],
        razon="Compra 100 acciones + vende call $345.00.",
        riesgo_maximo=32190.0, ganancia_maxima=2310.0, breakevens=[321.90],
        probabilidad_exito=0.6, valor_esperado=100.0, delta_neto=0.4, theta_neto=1.0,
        liquidez_score=80.0, capital_adicional_requerido=True,
    )
    monkeypatch.setattr(tc, "construir_estrategias", lambda cadena, spot: [covered_call])
    monkeypatch.setattr(tc, "rankear", lambda estrategias: estrategias)
    texto = tc.generar_trade("AAPL")
    assert "🔴 Si baja hacia $318" in texto
    assert "Pérdida estimada:" in texto


def test_coherencia_estrategia_alineada_con_tesis_muestra_mejor_estrategia(monkeypatch):
    _mock_red_basica(monkeypatch)
    monkeypatch.setattr(tc, "_tesis_categoria", lambda *a, **k: "alcista")
    long_call = EstrategiaOpciones(
        nombre="Long Call", patas=[PataOpcion("call", "comprar", 100.0, 5.0)],
        razon="Compra call $100.00.", riesgo_maximo=500.0, ganancia_maxima=None,
        breakevens=[105.0], probabilidad_exito=0.4, valor_esperado=50.0,
        delta_neto=0.5, theta_neto=-1.0, liquidez_score=80.0,
    )
    monkeypatch.setattr(tc, "construir_estrategias", lambda cadena, spot: [long_call])
    monkeypatch.setattr(tc, "rankear", lambda estrategias: estrategias)
    texto = tc.generar_trade("AAPL")
    assert "🎯 Mejor estrategia" in texto
    assert "no coincide con la tesis" not in texto


def test_coherencia_estrategia_no_alineada_cambia_encabezado_y_agrega_nota(monkeypatch):
    """Regresión del pedido explícito del usuario: evitar mensajes
    contradictorios ("No compraría hoy" + "Long Call" presentado como
    "Mejor estrategia") sin tocar el ranking matemático."""
    _mock_red_basica(monkeypatch)
    monkeypatch.setattr(tc, "_tesis_categoria", lambda *a, **k: "esperar")
    long_call = EstrategiaOpciones(
        nombre="Long Call", patas=[PataOpcion("call", "comprar", 100.0, 5.0)],
        razon="Compra call $100.00.", riesgo_maximo=500.0, ganancia_maxima=None,
        breakevens=[105.0], probabilidad_exito=0.4, valor_esperado=50.0,
        delta_neto=0.5, theta_neto=-1.0, liquidez_score=80.0,
    )
    monkeypatch.setattr(tc, "construir_estrategias", lambda cadena, spot: [long_call])
    monkeypatch.setattr(tc, "rankear", lambda estrategias: estrategias)
    texto = tc.generar_trade("AAPL")
    assert "🎯 Mejor estrategia" not in texto
    assert "Si decidieras operar hoy, la estrategia con mejor relación matemática es..." in texto
    assert "no coincide con la tesis principal del modelo (Esperar)" in texto
    assert "Long Call" in texto


def test_por_que_usa_precio_vs_objetivo_de_analistas(monkeypatch):
    _mock_red_basica(monkeypatch, fund=Fundamentales("AAPL", nombre="Apple Inc.", analista_precio_objetivo=80.0))
    texto = tc.generar_trade("AAPL")
    assert "objetivo promedio de analistas" in texto


def test_sin_objetivo_de_analistas_no_lo_menciona(monkeypatch):
    _mock_red_basica(monkeypatch, fund=Fundamentales("AAPL", nombre="Apple Inc.", analista_precio_objetivo=None))
    texto = tc.generar_trade("AAPL")
    assert "objetivo promedio de analistas" not in texto


def test_riesgo_bajo_sin_banderas_reales(monkeypatch):
    _mock_red_basica(monkeypatch)
    texto = tc.generar_trade("AAPL")
    assert "Sin banderas de riesgo técnico relevantes detectadas hoy." in texto or "•" in texto


# ------------------------- funciones auxiliares deterministas -------------------------


def test_emoji_convic_umbrales():
    assert tc._emoji_convic(84) == "🟢"
    assert tc._emoji_convic(50) == "🟡"
    assert tc._emoji_convic(10) == "🔴"


def test_fmt_strike_entero_vs_decimal():
    assert tc._fmt_strike(330.0) == "330"
    assert tc._fmt_strike(327.5) == "327.50"


def test_fmt_signed_round():
    assert tc._fmt_signed_round(427.0) == "+$427"
    assert tc._fmt_signed_round(-573.0) == "-$573"


def test_emoji_payoff_usa_el_signo_real_no_una_direccion_asumida():
    """Regresión: el emoji del escenario debe reflejar el payoff real
    calculado, no la dirección que en teoría favorece a la estrategia --
    el precio objetivo real de analistas puede apuntar en la dirección
    que técnicamente perjudica a la estrategia elegida."""
    assert tc._emoji_payoff(427.0) == "🟢"
    assert tc._emoji_payoff(-365.0) == "🔴"
    assert tc._emoji_payoff(0.0) == "🟢"


def test_punto_equilibrio_un_breakeven():
    assert tc._punto_equilibrio([334.27]) == "$334.27"


def test_punto_equilibrio_dos_breakevens():
    assert tc._punto_equilibrio([90.0, 110.0]) == "$90.00 - $110.00"


def test_punto_equilibrio_vacio():
    assert tc._punto_equilibrio([]) == "No disponible"


def test_precio_adverso_usa_signo_del_delta_neto():
    assert tc._precio_adverso(100.0, delta_neto=0.5) == 85.0
    assert tc._precio_adverso(100.0, delta_neto=-0.5) == pytest.approx(115.0)
    assert tc._precio_adverso(100.0, delta_neto=None) == 85.0


def test_precio_favorable_es_espejo_del_adverso():
    assert tc._precio_favorable(100.0, delta_neto=0.5) == pytest.approx(115.0)
    assert tc._precio_favorable(100.0, delta_neto=-0.5) == pytest.approx(85.0)


def test_tesis_categoria_alcista_sobrecomprado_es_esperar():
    assert tc._tesis_categoria("alcista", "Exigente", rsi=80.0) == "esperar"
    assert tc._tesis_categoria("alcista", "Razonable", rsi=90.0) == "esperar"


def test_tesis_categoria_alcista_sano():
    assert tc._tesis_categoria("alcista", "Razonable", rsi=50.0) == "alcista"


def test_tesis_categoria_bajista_y_neutral_pasan_directo():
    assert tc._tesis_categoria("bajista", "Razonable", rsi=50.0) == "bajista"
    assert tc._tesis_categoria("neutral", "Razonable", rsi=50.0) == "neutral"


def test_tesis_categoria_desconocida_es_no_determinable():
    assert tc._tesis_categoria("rara", "Razonable", rsi=50.0) == "no_determinable"


def test_tesis_coincide_neutral_y_no_determinable_siempre_coinciden():
    assert tc._tesis_coincide_con_estrategia("neutral", "alcista") is True
    assert tc._tesis_coincide_con_estrategia("neutral", "bajista") is True
    assert tc._tesis_coincide_con_estrategia("no_determinable", "bajista") is True


def test_tesis_coincide_esperar_rechaza_alcista_acepta_el_resto():
    assert tc._tesis_coincide_con_estrategia("esperar", "alcista") is False
    assert tc._tesis_coincide_con_estrategia("esperar", "bajista") is True
    assert tc._tesis_coincide_con_estrategia("esperar", "neutral") is True


def test_tesis_coincide_alcista_bajista_exigen_coincidencia_exacta():
    assert tc._tesis_coincide_con_estrategia("alcista", "alcista") is True
    assert tc._tesis_coincide_con_estrategia("alcista", "bajista") is False
    assert tc._tesis_coincide_con_estrategia("bajista", "bajista") is True
    assert tc._tesis_coincide_con_estrategia("bajista", "alcista") is False


def test_conclusion_acciones_esperar():
    preambulo, veredicto = tc._conclusion_acciones("esperar")
    assert preambulo == "Esperaría una pequeña corrección antes de entrar."
    assert veredicto == "No compraría acciones hoy."


def test_conclusion_acciones_alcista_sano():
    preambulo, veredicto = tc._conclusion_acciones("alcista")
    assert preambulo is None
    assert "Compraría acciones hoy" in veredicto


def test_conclusion_acciones_bajista():
    _, veredicto = tc._conclusion_acciones("bajista")
    assert "No compraría acciones hoy" in veredicto


def test_conclusion_acciones_neutral():
    _, veredicto = tc._conclusion_acciones("neutral")
    assert "No hay una señal técnica clara" in veredicto


def test_conclusion_opciones_sin_estrategias():
    assert "No hay estrategias de opciones disponibles hoy" in tc._conclusion_opciones([], None)


def test_conclusion_opciones_score_bajo_es_cauteloso():
    texto = tc._conclusion_opciones(["placeholder"], 20)
    assert "con cautela" in texto


def test_conclusion_opciones_score_alto():
    texto = tc._conclusion_opciones(["placeholder"], 80)
    assert texto == "Sí investigaría una estrategia con opciones."


def test_por_que_bullets_sobrecompra():
    fund = Fundamentales("AAPL", analista_precio_objetivo=None)
    bullets = tc._por_que_bullets(rsi=80.0, fund=fund, spot=100.0, score_100=None)
    assert any("sobrecompra" in b for b in bullets)


def test_por_que_bullets_sobreventa():
    fund = Fundamentales("AAPL", analista_precio_objetivo=None)
    bullets = tc._por_que_bullets(rsi=20.0, fund=fund, spot=100.0, score_100=None)
    assert any("sobreventa" in b for b in bullets)


def test_por_que_bullets_precio_sobre_objetivo():
    fund = Fundamentales("AAPL", analista_precio_objetivo=90.0)
    bullets = tc._por_que_bullets(rsi=None, fund=fund, spot=100.0, score_100=None)
    assert any("por encima del objetivo" in b for b in bullets)


def test_por_que_bullets_precio_bajo_objetivo():
    fund = Fundamentales("AAPL", analista_precio_objetivo=120.0)
    bullets = tc._por_que_bullets(rsi=None, fund=fund, spot=100.0, score_100=None)
    assert any("por debajo del objetivo" in b for b in bullets)


def test_por_que_bullets_score_alto_y_bajo():
    fund = Fundamentales("AAPL", analista_precio_objetivo=None)
    altos = tc._por_que_bullets(rsi=None, fund=fund, spot=100.0, score_100=80)
    bajos = tc._por_que_bullets(rsi=None, fund=fund, spot=100.0, score_100=10)
    assert any("Buena relación riesgo/beneficio" in b for b in altos)
    assert any("modesta" in b for b in bajos)
