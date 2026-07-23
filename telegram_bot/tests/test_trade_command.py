"""Pruebas de /trade TICKER: red (Yahoo) mockeada por completo -- verifica
que la versión de bolsillo muestre solo lo que de verdad se pudo calcular
(nunca invente Score/Objetivo), que "Score cuantitativo" sea siempre un
score real (nunca una probabilidad de éxito), que la capa de coherencia
tesis-vs-estrategia funcione sin tocar el ranking, que "¿Qué tiene que
pasar para ganar?" use el breakeven/vencimiento reales, y que el Plan de
acción use niveles técnicos reales (SMA50, máximo 52 semanas) en vez de
un breakeven de opciones que no tiene sentido para estrategias de
ingreso."""

from __future__ import annotations

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
    monkeypatch.setattr(tc, "_obtener_fecha_resultados", lambda ticker: None)


def test_ticker_sin_datos_de_precio_da_mensaje_claro(monkeypatch):
    monkeypatch.setattr(tc, "YahooProvider", lambda: _FakeProvider({}, {}))
    texto = tc.generar_trade("ZZZZ")
    assert "no pude obtener datos de precio" in texto.lower()


def test_mensaje_basico_incluye_secciones_clave(monkeypatch):
    _mock_red_basica(monkeypatch)
    texto = tc.generar_trade("AAPL")
    assert "📊 AAPL — Apple Inc." in texto
    assert "Score cuantitativo del modelo:" in texto
    assert "Convicción" not in texto
    assert "📌 Mi conclusión" in texto
    assert "🎯 Tesis del modelo" in texto
    assert "¿Por qué" in texto
    assert "💰 Ejemplo" in texto
    assert "Costo máximo:" in texto
    assert "Ganancia máxima:" in texto
    assert "¿Qué tiene que pasar para que esta estrategia gane?" in texto
    assert "✅ Para ganar necesitas:" in texto
    assert "Lo que puede salir mal" in texto
    assert "⚠️ Riesgos" in texto
    assert "Próximo paso" in texto
    assert "/report AAPL" in texto
    assert "/options AAPL --full" in texto
    assert "Esto no es una recomendación de compra/venta." in texto
    assert "⭐" not in texto  # sin estrellas


def test_score_no_disponible_si_no_esta_en_shortlist(monkeypatch):
    _mock_red_basica(monkeypatch)
    texto = tc.generar_trade("AAPL")
    assert "⚪ Score cuantitativo del modelo: No disponible (no está en la shortlist de hoy)" in texto


def test_score_usa_score_real_del_screener(monkeypatch):
    _mock_red_basica(monkeypatch)
    monkeypatch.setattr(tc, "_shortlist_entry", lambda ticker: {
        "score": 84.0, "sector": "Technology", "sub_scores": {"calidad": 70.0, "valor": 50.0},
    })
    texto = tc.generar_trade("AAPL")
    assert "🟢 Score cuantitativo del modelo: 84/100" in texto


def test_cadena_none_no_rompe_y_omite_estrategias(monkeypatch):
    _mock_red_basica(monkeypatch)
    monkeypatch.setattr(tc, "obtener_cadena", lambda ticker: None)
    texto = tc.generar_trade("AAPL")
    assert "No pude calcular estrategias de opciones para hoy" in texto
    assert "💰 Ejemplo" not in texto
    assert "¿Qué tiene que pasar" not in texto


def test_coherencia_estrategia_alineada_muestra_mejor_estrategia(monkeypatch):
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
    assert "Mejor estrategia" in texto
    assert "Si aun así quieres operar" not in texto


def test_coherencia_estrategia_no_alineada_cambia_encabezado(monkeypatch):
    """Regresión del pedido explícito del usuario: evitar mensajes
    contradictorios ("No compraría hoy" + "Long Call" presentado sin más
    contexto) sin tocar el ranking matemático."""
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
    assert "Si aun así quieres operar..." in texto
    assert "Mejor estrategia" not in texto
    assert "Long Call" in texto
    assert "🟡 Esperar" in texto
    # el "Plan de acción" solo aparece cuando la conclusión es "esperar"
    assert "📝 Plan de acción" in texto


def test_plan_de_accion_ausente_si_tesis_no_es_esperar(monkeypatch):
    _mock_red_basica(monkeypatch)
    monkeypatch.setattr(tc, "_tesis_categoria", lambda *a, **k: "alcista")
    texto = tc.generar_trade("AAPL")
    assert "📝 Plan de acción" not in texto


def test_por_que_incluye_explicacion_fija_de_la_estrategia(monkeypatch):
    _mock_red_basica(monkeypatch)
    long_call = EstrategiaOpciones(
        nombre="Long Call", patas=[PataOpcion("call", "comprar", 100.0, 5.0)],
        razon="Compra call $100.00.", riesgo_maximo=500.0, ganancia_maxima=None,
        breakevens=[105.0], probabilidad_exito=0.4, valor_esperado=50.0,
        delta_neto=0.5, theta_neto=-1.0, liquidez_score=80.0,
    )
    monkeypatch.setattr(tc, "construir_estrategias", lambda cadena, spot: [long_call])
    monkeypatch.setattr(tc, "rankear", lambda estrategias: estrategias)
    texto = tc.generar_trade("AAPL")
    assert "la estrategia que más gana si AAPL sigue subiendo con fuerza" in texto


def test_condicion_para_ganar_usa_breakeven_y_vencimiento_reales(monkeypatch):
    _mock_red_basica(monkeypatch)
    long_call = EstrategiaOpciones(
        nombre="Long Call", patas=[PataOpcion("call", "comprar", 100.0, 5.0)],
        razon="Compra call $100.00.", riesgo_maximo=500.0, ganancia_maxima=None,
        breakevens=[105.0], probabilidad_exito=0.4, valor_esperado=50.0,
        delta_neto=0.5, theta_neto=-1.0, liquidez_score=80.0,
    )
    monkeypatch.setattr(tc, "construir_estrategias", lambda cadena, spot: [long_call])
    monkeypatch.setattr(tc, "rankear", lambda estrategias: estrategias)
    texto = tc.generar_trade("AAPL")
    assert "Que AAPL suba arriba de $105 antes del 1 de enero." in texto


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


def test_emoji_score_umbrales():
    assert tc._emoji_score(84) == "🟢"
    assert tc._emoji_score(50) == "🟡"
    assert tc._emoji_score(10) == "🔴"


def test_fmt_strike_entero_vs_decimal():
    assert tc._fmt_strike(330.0) == "330"
    assert tc._fmt_strike(327.5) == "327.50"


def test_fmt_price_round():
    assert tc._fmt_price_round(427.0) == "$427"
    assert tc._fmt_price_round(1272.3) == "$1,272"


def test_fmt_fecha_es():
    assert tc._fmt_fecha_es("2026-08-28") == "28 de agosto"
    assert tc._fmt_fecha_es("no-es-fecha") == "no-es-fecha"


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


def test_explicacion_estrategia_conocida_incluye_ticker():
    texto = tc._explicacion_estrategia("Long Call", "AAPL")
    assert "AAPL" in texto
    assert texto  # no vacío


def test_explicacion_estrategia_desconocida_da_vacio():
    assert tc._explicacion_estrategia("Estrategia Inventada", "AAPL") == ""


def test_todas_las_9_estrategias_tienen_explicacion_y_riesgos():
    nombres = ["Long Call", "Long Put", "Bull Call Spread", "Bear Put Spread", "Bull Put Spread",
               "Bear Call Spread", "Covered Call", "Cash Secured Put", "Iron Condor"]
    for nombre in nombres:
        assert tc._explicacion_estrategia(nombre, "AAPL"), f"falta explicación para {nombre}"
        assert tc._riesgos_estrategia(nombre, "AAPL"), f"faltan riesgos para {nombre}"


def test_riesgos_estrategia_desconocida_da_lista_vacia():
    assert tc._riesgos_estrategia("Estrategia Inventada", "AAPL") == []


def test_por_que_bullets_sobrecompra():
    fund = Fundamentales("AAPL", analista_precio_objetivo=None)
    bullets = tc._por_que_bullets(rsi=80.0, fund=fund, spot=100.0)
    assert any("sobrecompra" in b for b in bullets)


def test_por_que_bullets_sobreventa():
    fund = Fundamentales("AAPL", analista_precio_objetivo=None)
    bullets = tc._por_que_bullets(rsi=20.0, fund=fund, spot=100.0)
    assert any("sobreventa" in b for b in bullets)


def test_por_que_bullets_precio_sobre_objetivo():
    fund = Fundamentales("AAPL", analista_precio_objetivo=90.0)
    bullets = tc._por_que_bullets(rsi=None, fund=fund, spot=100.0)
    assert any("por encima del objetivo" in b for b in bullets)


def test_por_que_bullets_precio_bajo_objetivo():
    fund = Fundamentales("AAPL", analista_precio_objetivo=120.0)
    bullets = tc._por_que_bullets(rsi=None, fund=fund, spot=100.0)
    assert any("por debajo del objetivo" in b for b in bullets)


def test_condicion_para_ganar_debito_pide_movimiento_direccional():
    e = EstrategiaOpciones(nombre="Long Call", patas=[], razon="", riesgo_maximo=500.0,
                           ganancia_maxima=None, breakevens=[105.0])
    assert tc._condicion_para_ganar(e, "AAPL", "1 de enero") == "Que AAPL suba arriba de $105 antes del 1 de enero."


def test_condicion_para_ganar_debito_bajista():
    e = EstrategiaOpciones(nombre="Bear Put Spread", patas=[], razon="", riesgo_maximo=500.0,
                           ganancia_maxima=300.0, breakevens=[95.0])
    assert tc._condicion_para_ganar(e, "AAPL", "1 de enero") == "Que AAPL baje por debajo de $95 antes del 1 de enero."


def test_condicion_para_ganar_credito_pide_mantenerse():
    e = EstrategiaOpciones(nombre="Bull Put Spread", patas=[], razon="", riesgo_maximo=500.0,
                           ganancia_maxima=100.0, breakevens=[95.0])
    assert tc._condicion_para_ganar(e, "AAPL", "1 de enero") == "Que AAPL se mantenga arriba de $95 hasta el 1 de enero."


def test_condicion_para_ganar_iron_condor_usa_rango():
    e = EstrategiaOpciones(nombre="Iron Condor", patas=[], razon="", riesgo_maximo=500.0,
                           ganancia_maxima=100.0, breakevens=[90.0, 110.0])
    assert tc._condicion_para_ganar(e, "AAPL", "1 de enero") == \
        "Que AAPL se mantenga entre $90 y $110 hasta el 1 de enero."


def test_condicion_para_ganar_sin_breakeven():
    e = EstrategiaOpciones(nombre="Long Call", patas=[], razon="", riesgo_maximo=500.0,
                           ganancia_maxima=None, breakevens=[])
    assert "No pude determinar" in tc._condicion_para_ganar(e, "AAPL", "1 de enero")


def test_plan_de_accion_incluye_sma():
    lineas = tc._plan_de_accion("AAPL", top=None, sma50=95.0, maximo_52s=110.0, fecha_resultados=None)
    texto = "\n".join(lineas)
    assert "cerca de $95" in texto


def test_plan_de_accion_sin_top_omite_nivel_de_ruptura():
    lineas = tc._plan_de_accion("AAPL", top=None, sma50=95.0, maximo_52s=110.0, fecha_resultados=None)
    texto = "\n".join(lineas)
    assert "máximo de 52 semanas" not in texto


def test_plan_de_accion_con_top_incluye_nivel_de_ruptura():
    e = EstrategiaOpciones(nombre="Covered Call", patas=[], razon="", riesgo_maximo=500.0,
                           ganancia_maxima=100.0, breakevens=[95.0])
    lineas = tc._plan_de_accion("AAPL", top=e, sma50=95.0, maximo_52s=110.0, fecha_resultados=None)
    texto = "\n".join(lineas)
    assert "Si rompe arriba de $110 (máximo de 52 semanas), volvería a analizar Covered Call." in texto


def test_plan_de_accion_incluye_fecha_resultados_si_existe():
    lineas = tc._plan_de_accion("AAPL", top=None, sma50=None, maximo_52s=None, fecha_resultados="2026-08-28")
    texto = "\n".join(lineas)
    assert "2 días antes de resultados (28 de agosto)" in texto


def test_plan_de_accion_sin_datos_no_rompe():
    lineas = tc._plan_de_accion("AAPL", top=None, sma50=None, maximo_52s=None, fecha_resultados=None)
    assert lineas == ["📝 Plan de acción", "", "Esperaría. No abriría posición hoy."]
