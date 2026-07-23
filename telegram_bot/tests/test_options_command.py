"""Pruebas de /options TICKER: red (Yahoo/Anthropic) mockeada por
completo -- verifica que el memo diga lo que de verdad se pudo calcular
(nunca invente IV Rank), que el filtro de lenguaje de recomendación del
LLM funcione (belt-and-suspenders, igual que news_analyst/explicador.py),
y que --simple/--full muestren la cantidad de detalle correcta."""

from __future__ import annotations

import options_command as oc
from screener.data.provider import Barras, Fundamentales
from screener.options_ideas import CadenaOpciones, ContratoOpcion
from screener.options_math import precio_black_scholes
from screener.options_strategies import EstrategiaOpciones

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


def test_ticker_sin_datos_de_precio_da_mensaje_claro(monkeypatch):
    monkeypatch.setattr(oc, "YahooProvider", lambda: _FakeProvider({}, {}))
    texto = oc.generar_options("ZZZZ")
    assert "no pude obtener datos de precio" in texto.lower()


def test_cadena_none_da_mensaje_claro(monkeypatch):
    monkeypatch.setattr(oc, "YahooProvider",
                        lambda: _FakeProvider({"AAPL": _barras()}, {"AAPL": Fundamentales("AAPL")}))
    monkeypatch.setattr(oc, "obtener_cadena", lambda ticker: None)
    texto = oc.generar_options("AAPL")
    assert "no pude obtener la cadena de opciones" in texto.lower()


def test_cadena_sin_estrategias_da_mensaje_claro(monkeypatch):
    monkeypatch.setattr(oc, "YahooProvider",
                        lambda: _FakeProvider({"AAPL": _barras()}, {"AAPL": Fundamentales("AAPL")}))
    vacia = CadenaOpciones(ticker="AAPL", vencimiento="2099-01-01", dias_a_vencimiento=35, calls=[], puts=[])
    monkeypatch.setattr(oc, "obtener_cadena", lambda ticker: vacia)
    texto = oc.generar_options("AAPL")
    assert "no pude construir estrategias" in texto.lower()


def test_modo_simple_muestra_top4_y_footer(monkeypatch):
    monkeypatch.setattr(oc, "YahooProvider",
                        lambda: _FakeProvider({"AAPL": _barras()}, {"AAPL": Fundamentales("AAPL", nombre="Apple Inc.")}))
    monkeypatch.setattr(oc, "obtener_cadena", lambda ticker: _cadena_sintetica(ticker))
    monkeypatch.setattr(oc, "llamar_claude", lambda system, user: "Explicación de prueba, sin lenguaje prohibido.")

    texto = oc.generar_options("AAPL", modo="simple")
    assert "📐 Opciones: Apple Inc. (AAPL)" in texto
    assert "Tesis técnica:" in texto
    assert "IV: " in texto
    assert "IV Rank: No disponible" in texto
    assert "estrategias que sí tienen sentido para una tesis" in texto.lower()
    assert "Ver las" in texto and "--full" in texto
    assert "💬 Explicación:" in texto
    assert "Explicación de prueba" in texto
    assert oc.DISCLAIMER in texto
    # score numérico real, no estrellas
    assert "-- Score: " in texto
    assert "⭐" not in texto
    # modo simple no debe mostrar el desglose completo (Greeks/breakeven detallado)
    assert "Delta neto:" not in texto


def test_modo_full_muestra_todas_las_estrategias_con_detalle(monkeypatch):
    monkeypatch.setattr(oc, "YahooProvider",
                        lambda: _FakeProvider({"AAPL": _barras()}, {"AAPL": Fundamentales("AAPL")}))
    monkeypatch.setattr(oc, "obtener_cadena", lambda ticker: _cadena_sintetica(ticker))
    monkeypatch.setattr(oc, "llamar_claude", lambda system, user: "Explicación completa.")

    texto = oc.generar_options("AAPL", modo="full")
    assert "Ranking completo (" in texto
    assert "Delta neto:" in texto
    assert "Probabilidad de éxito" in texto
    assert "Valor esperado:" in texto
    assert "Liquidez (aprox.):" in texto
    assert "-- Score: " in texto
    assert "⭐" not in texto
    assert "... y" not in texto  # full no trunca


def test_explicacion_ausente_sin_api_key_no_rompe(monkeypatch):
    monkeypatch.setattr(oc, "YahooProvider",
                        lambda: _FakeProvider({"AAPL": _barras()}, {"AAPL": Fundamentales("AAPL")}))
    monkeypatch.setattr(oc, "obtener_cadena", lambda ticker: _cadena_sintetica(ticker))
    monkeypatch.setattr(oc, "llamar_claude", lambda system, user: None)

    texto = oc.generar_options("AAPL")
    assert "no disponible" in texto.lower()
    assert "ANTHROPIC_API_KEY" in texto


def test_explicacion_con_lenguaje_de_recomendacion_se_descarta(monkeypatch):
    monkeypatch.setattr(oc, "YahooProvider",
                        lambda: _FakeProvider({"AAPL": _barras()}, {"AAPL": Fundamentales("AAPL")}))
    monkeypatch.setattr(oc, "obtener_cadena", lambda ticker: _cadena_sintetica(ticker))
    monkeypatch.setattr(oc, "llamar_claude",
                        lambda system, user: "Te recomiendo comprar esta estrategia ya mismo.")

    texto = oc.generar_options("AAPL")
    assert "Te recomiendo comprar" not in texto
    assert "no pasó el filtro de seguridad" in texto.lower() or "no disponible" in texto.lower()


def test_filtrar_explicacion_vacia_da_none():
    assert oc._filtrar_explicacion(None) is None
    assert oc._filtrar_explicacion("") is None


def test_tesis_desconocida_da_no_determinable():
    assert oc._tesis("desconocida") == "No determinable"
    assert oc._tesis("alcista") == "Alcista"


# ------------------------- coherencia con la tesis -------------------------

def test_coincide_con_tesis_neutral_y_no_determinable_siempre_coinciden():
    assert oc._coincide_con_tesis("neutral", "alcista") is True
    assert oc._coincide_con_tesis("neutral", "bajista") is True
    assert oc._coincide_con_tesis("no_determinable", "bajista") is True


def test_coincide_con_tesis_direccion_neutral_siempre_coincide():
    """Una estrategia de dirección "neutral" (ej. Iron Condor) no
    contradice ninguna tesis direccional -- apuesta a que el precio se
    quede quieto, no a una dirección."""
    assert oc._coincide_con_tesis("alcista", "neutral") is True
    assert oc._coincide_con_tesis("bajista", "neutral") is True


def test_coincide_con_tesis_exige_coincidencia_exacta_si_ambas_son_direccionales():
    assert oc._coincide_con_tesis("alcista", "alcista") is True
    assert oc._coincide_con_tesis("alcista", "bajista") is False
    assert oc._coincide_con_tesis("bajista", "bajista") is True
    assert oc._coincide_con_tesis("bajista", "alcista") is False


def test_modo_simple_oculta_estrategias_de_direccion_contraria(monkeypatch):
    """Regresión del caso real reportado: Top 1 "Bear Put Spread"
    (bajista) con la tesis técnica en "Alcista" en el mismo mensaje
    rompía la confianza -- el modo simple debe ocultar las estrategias
    bajistas cuando la tesis es alcista, sin tocar el ranking."""
    monkeypatch.setattr(oc, "YahooProvider",
                        lambda: _FakeProvider({"AAPL": _barras()}, {"AAPL": Fundamentales("AAPL", nombre="Apple Inc.")}))
    monkeypatch.setattr(oc, "obtener_cadena", lambda ticker: _cadena_sintetica(ticker))
    monkeypatch.setattr(oc, "llamar_claude", lambda system, user: "Explicación.")
    monkeypatch.setattr(oc, "clasificar_tendencia", lambda score: "alcista")

    long_call = EstrategiaOpciones(nombre="Long Call", patas=[], razon="", riesgo_maximo=500.0,
                                   ganancia_maxima=None, breakevens=[105.0])
    bear_put = EstrategiaOpciones(nombre="Bear Put Spread", patas=[], razon="", riesgo_maximo=300.0,
                                  ganancia_maxima=200.0, breakevens=[95.0])
    monkeypatch.setattr(oc, "construir_estrategias", lambda cadena, spot: [bear_put, long_call])
    monkeypatch.setattr(oc, "rankear", lambda estrategias: [bear_put, long_call])  # bajista puntúa más alto

    texto = oc.generar_options("AAPL", modo="simple")
    assert "Long Call" in texto
    assert "Bear Put Spread" not in texto
    assert "Ver las 2 estrategias completas" in texto


def test_modo_full_no_filtra_por_tesis(monkeypatch):
    monkeypatch.setattr(oc, "YahooProvider",
                        lambda: _FakeProvider({"AAPL": _barras()}, {"AAPL": Fundamentales("AAPL", nombre="Apple Inc.")}))
    monkeypatch.setattr(oc, "obtener_cadena", lambda ticker: _cadena_sintetica(ticker))
    monkeypatch.setattr(oc, "llamar_claude", lambda system, user: "Explicación.")
    monkeypatch.setattr(oc, "clasificar_tendencia", lambda score: "alcista")

    long_call = EstrategiaOpciones(nombre="Long Call", patas=[], razon="", riesgo_maximo=500.0,
                                   ganancia_maxima=None, breakevens=[105.0])
    bear_put = EstrategiaOpciones(nombre="Bear Put Spread", patas=[], razon="", riesgo_maximo=300.0,
                                  ganancia_maxima=200.0, breakevens=[95.0])
    monkeypatch.setattr(oc, "construir_estrategias", lambda cadena, spot: [bear_put, long_call])
    monkeypatch.setattr(oc, "rankear", lambda estrategias: [bear_put, long_call])

    texto = oc.generar_options("AAPL", modo="full")
    assert "Bear Put Spread" in texto
    assert "Long Call" in texto


def test_modo_simple_sin_ninguna_estrategia_alineada_cae_a_top_normal(monkeypatch):
    monkeypatch.setattr(oc, "YahooProvider",
                        lambda: _FakeProvider({"AAPL": _barras()}, {"AAPL": Fundamentales("AAPL", nombre="Apple Inc.")}))
    monkeypatch.setattr(oc, "obtener_cadena", lambda ticker: _cadena_sintetica(ticker))
    monkeypatch.setattr(oc, "llamar_claude", lambda system, user: "Explicación.")
    monkeypatch.setattr(oc, "clasificar_tendencia", lambda score: "alcista")

    bear_put = EstrategiaOpciones(nombre="Bear Put Spread", patas=[], razon="", riesgo_maximo=300.0,
                                  ganancia_maxima=200.0, breakevens=[95.0])
    monkeypatch.setattr(oc, "construir_estrategias", lambda cadena, spot: [bear_put])
    monkeypatch.setattr(oc, "rankear", lambda estrategias: [bear_put])

    texto = oc.generar_options("AAPL", modo="simple")
    assert "Ninguna estrategia coincide con la tesis" in texto
    assert "Bear Put Spread" in texto
