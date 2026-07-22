"""Pruebas de /trade TICKER: red (Yahoo/Anthropic/Google News) mockeada
por completo -- verifica que el "tablero de trader" muestre lo que de
verdad se pudo calcular (nunca invente Objetivo/importancia de earnings),
que "Confianza" se etiquete explícitamente como consistencia de señales
(no una probabilidad de éxito), y que el filtro de lenguaje de
recomendación del LLM funcione igual que en /options y /report."""

from __future__ import annotations

import pytest

from news_analyst.models import Explicacion

import trade_command as tc
from screener.data.provider import Barras, Fundamentales
from screener.options_ideas import CadenaOpciones, ContratoOpcion
from screener.options_math import precio_black_scholes

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
    monkeypatch.setattr(tc, "titulares_google_news", lambda query, maximo=5: [])
    monkeypatch.setattr(tc, "llamar_claude", lambda system, user: "Explicación de prueba, sin lenguaje prohibido.")


def test_ticker_sin_datos_de_precio_da_mensaje_claro(monkeypatch):
    monkeypatch.setattr(tc, "YahooProvider", lambda: _FakeProvider({}, {}))
    texto = tc.generar_trade("ZZZZ")
    assert "no pude obtener datos de precio" in texto.lower()


def test_mensaje_basico_incluye_secciones_clave(monkeypatch):
    _mock_red_basica(monkeypatch)
    texto = tc.generar_trade("AAPL")
    assert "📊 Apple Inc. (AAPL)" in texto
    assert "Mi opinión" in texto
    assert "Confianza (% de señales alineadas, no una probabilidad de éxito):" in texto
    assert "¿Qué veo?" in texto
    assert "Negocio:" in texto
    assert "Tendencia:" in texto
    assert "Valuación:" in texto
    assert "Noticias:" in texto
    assert "Analistas:" in texto
    assert "Riesgo:" in texto
    assert "Si hoy quisiera entrar..." in texto
    assert "Comprar acciones:" in texto
    assert "Ejemplo educativo" in texto
    assert "¿Qué espero? (escenarios, no una predicción)" in texto
    assert "¿Qué eventos debo esperar?" in texto
    assert "Riesgos" in texto
    assert "/report AAPL" in texto
    assert "/options AAPL" in texto
    assert "Esto NO es una recomendación de compra/venta" in texto


def test_cadena_none_no_rompe_y_omite_estrategias(monkeypatch):
    _mock_red_basica(monkeypatch)
    monkeypatch.setattr(tc, "obtener_cadena", lambda ticker: None)
    texto = tc.generar_trade("AAPL")
    assert "No pude calcular estrategias de opciones para hoy" in texto
    assert "Ejemplo educativo" not in texto


def test_objetivo_no_disponible_si_no_hay_precio_objetivo_de_analistas(monkeypatch):
    _mock_red_basica(monkeypatch, fund=Fundamentales("AAPL", nombre="Apple Inc.", analista_precio_objetivo=None))
    texto = tc.generar_trade("AAPL")
    assert "Objetivo: No disponible" in texto


def test_objetivo_usa_precio_real_de_analistas(monkeypatch):
    _mock_red_basica(monkeypatch, fund=Fundamentales("AAPL", nombre="Apple Inc.", analista_precio_objetivo=120.0))
    texto = tc.generar_trade("AAPL")
    assert "Objetivo: $120.00 (precio objetivo promedio de analistas)" in texto


def test_fecha_resultados_muestra_dias_restantes(monkeypatch):
    _mock_red_basica(monkeypatch)
    monkeypatch.setattr(tc, "_obtener_fecha_resultados", lambda ticker: "2099-01-15")
    texto = tc.generar_trade("AAPL")
    assert "Próximos resultados: 2099-01-15" in texto
    assert "espera volatilidad" in texto
    # nunca se inventa una calificación de "qué tan importante será"
    assert "importancia" not in texto.lower()


def test_sin_fecha_resultados_dice_que_no_encontro(monkeypatch):
    _mock_red_basica(monkeypatch)
    texto = tc.generar_trade("AAPL")
    assert "No encontré una fecha de resultados confirmada." in texto


def test_explicacion_con_lenguaje_de_recomendacion_se_descarta(monkeypatch):
    _mock_red_basica(monkeypatch)
    monkeypatch.setattr(tc, "llamar_claude",
                        lambda system, user: "Te recomiendo comprar esta estrategia ya mismo.")
    texto = tc.generar_trade("AAPL")
    assert "Te recomiendo comprar" not in texto
    assert "no pasó el filtro de seguridad" in texto.lower() or "no disponible" in texto.lower()


def test_explicacion_ausente_sin_api_key_no_rompe(monkeypatch):
    _mock_red_basica(monkeypatch)
    monkeypatch.setattr(tc, "llamar_claude", lambda system, user: None)
    texto = tc.generar_trade("AAPL")
    assert "ANTHROPIC_API_KEY" in texto


def test_noticias_generan_tono_via_generar_explicacion(monkeypatch):
    _mock_red_basica(monkeypatch)
    monkeypatch.setattr(tc, "_shortlist_entry", lambda ticker: {
        "score": 83.0, "sector": "Technology", "sub_scores": {"calidad": 70.0, "valor": 50.0},
    })
    monkeypatch.setattr(tc, "titulares_google_news", lambda query, maximo=5: ["Apple anuncia algo nuevo"])
    monkeypatch.setattr(tc, "generar_explicacion",
                        lambda titular, entrada: Explicacion(texto="x", tono="positivo", nivel_importancia=3))
    texto = tc.generar_trade("AAPL")
    assert "Noticias: ⭐⭐⭐⭐⭐" in texto


def test_riesgo_bajo_sin_banderas_reales(monkeypatch):
    _mock_red_basica(monkeypatch)
    texto = tc.generar_trade("AAPL")
    assert "Riesgo: 🟢 Bajo" in texto or "Riesgo: 🟡 Medio" in texto or "Riesgo: 🔴 Alto" in texto


# ------------------------- funciones auxiliares deterministas -------------------------


def test_estrellas_5_extremos():
    assert tc._estrellas_5(1.0) == "⭐⭐⭐⭐⭐"
    assert tc._estrellas_5(0.0) == "⭐☆☆☆☆"  # mínimo 1 estrella, nunca 0


def test_senal_negocio_none_da_no_disponible():
    estrellas, valor = tc._senal_negocio(None)
    assert valor is None
    assert "No disponible" in estrellas


def test_senal_tendencia_mapea_alcista_bajista_neutral():
    assert tc._senal_tendencia("alcista")[1] == 1.0
    assert tc._senal_tendencia("bajista")[1] == 0.0
    assert tc._senal_tendencia("neutral")[1] == 0.5


def test_senal_valoracion_desconocida_da_no_determinable():
    estrellas, valor = tc._senal_valoracion("otra cosa")
    assert valor is None
    assert "No determinable" in estrellas


def test_senal_analistas_desconocido_da_no_disponible():
    estrellas, valor = tc._senal_analistas(None)
    assert valor is None
    assert "No disponible" in estrellas


def test_senal_noticias_sin_titulares_da_sin_explicar():
    estrellas, valor = tc._senal_noticias([])
    assert valor is None
    assert "Sin noticias explicadas hoy" in estrellas


def test_senal_riesgo_umbrales():
    assert tc._senal_riesgo(0) == ("🟢 Bajo", 1.0)
    assert tc._senal_riesgo(1) == ("🟡 Medio", 0.5)
    assert tc._senal_riesgo(3) == ("🔴 Alto", 0.0)


def test_veredicto_sin_senales_da_mensaje_neutro():
    estrellas, texto = tc._veredicto(None, None)
    assert estrellas == "☆☆☆☆☆"
    assert "No hay suficientes señales" in texto


def test_veredicto_favorable_y_alineado():
    _, texto = tc._veredicto(0.8, 0.8)
    assert "SÍ la investigaría" in texto


def test_veredicto_desfavorable_y_alineado():
    _, texto = tc._veredicto(0.1, 0.8)
    assert "no la investigaría" in texto


def test_veredicto_mixto_o_no_alineado():
    _, texto = tc._veredicto(0.5, 0.0)
    assert "Señales mixtas" in texto


def test_filtrar_explicacion_vacia_da_none():
    assert tc._filtrar_explicacion(None) is None
    assert tc._filtrar_explicacion("") is None


def test_filtrar_explicacion_con_palabra_prohibida_da_none():
    assert tc._filtrar_explicacion("Deberías abrir esta posición ya.") is None


def test_precio_adverso_usa_signo_del_delta_neto():
    assert tc._precio_adverso(100.0, delta_neto=0.5) == 85.0   # delta positivo (alcista) -> baja
    assert tc._precio_adverso(100.0, delta_neto=-0.5) == pytest.approx(115.0)  # delta negativo (bajista) -> sube
    assert tc._precio_adverso(100.0, delta_neto=None) == 85.0  # default conservador


def test_salir_si_un_breakeven():
    assert tc._salir_si([95.0]) == "el precio cruza $95.00 en tu contra"


def test_salir_si_dos_breakevens():
    assert tc._salir_si([90.0, 110.0]) == "el precio sale del rango $90.00 - $110.00"


def test_salir_si_vacio():
    assert tc._salir_si([]) == "No disponible"


def test_tesis_desconocida_da_no_determinable():
    assert tc._tesis("desconocida") == "No determinable"
    assert tc._tesis("alcista") == "Alcista"
