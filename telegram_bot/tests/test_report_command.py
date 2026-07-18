"""Pruebas de /report TICKER: red (Yahoo/yfinance/Google News/Anthropic)
mockeada por completo -- verifica que el memo diga lo que de verdad sabe y
nunca invente lo que no sabe (ej. si el ticker no está en la shortlist de
hoy, o si no hay noticias)."""

from __future__ import annotations

import json

import report_command as rc
from news_analyst.models import Explicacion
from screener.data.provider import Barras, Fundamentales


def _barras(ticker="AAPL", n=260, precio_final=200.0):
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


def _escribir_shortlist(tmp_path, filas):
    archivo = tmp_path / "shortlist_hoy.json"
    archivo.write_text(json.dumps({
        "fecha": "2026-07-16T12:00:00+00:00",
        "universo_escaneado": 480,
        "shortlist": filas,
    }))
    return archivo


def test_ticker_sin_datos_de_precio_da_mensaje_claro(monkeypatch):
    monkeypatch.setattr(rc, "YahooProvider", lambda: _FakeProvider({}, {}))
    texto = rc.generar_reporte("ZZZZ")
    assert "no pude obtener datos de precio" in texto.lower()


def test_ticker_fuera_de_la_shortlist_no_inventa_una_posicion(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "SHORTLIST_PATH", tmp_path / "no_existe.json")
    monkeypatch.setattr(rc, "YahooProvider",
                        lambda: _FakeProvider({"XOM": _barras("XOM")},
                                              {"XOM": Fundamentales("XOM", nombre="Exxon Mobil")}))
    monkeypatch.setattr(rc, "titulares_google_news", lambda query, maximo=5: [])

    texto = rc.generar_reporte("xom")  # minúsculas: debe normalizar
    assert "No está en la shortlist de hoy" in texto
    assert "Posición #" not in texto
    assert "Exxon Mobil (XOM)" in texto
    assert rc.DISCLAIMER in texto


def test_ticker_en_la_shortlist_muestra_posicion_y_razones(monkeypatch, tmp_path):
    _escribir_shortlist(tmp_path, [
        {"ticker": "BNY", "score": 83.0, "sector": "Financial Services",
         "sub_scores": {"momentum": 90, "calidad": 85}, "nombre": "BNY Mellon", "industria": "Banks"},
        {"ticker": "AAPL", "score": 81.0, "sector": "Technology", "sub_scores": {}},
    ])
    monkeypatch.setattr(rc, "SHORTLIST_PATH", tmp_path / "shortlist_hoy.json")
    monkeypatch.setattr(rc, "YahooProvider",
                        lambda: _FakeProvider({"AAPL": _barras("AAPL")}, {"AAPL": Fundamentales("AAPL")}))
    monkeypatch.setattr(rc, "titulares_google_news", lambda query, maximo=5: ["Apple anuncia algo nuevo"])
    monkeypatch.setattr(rc, "generar_explicacion",
                        lambda titular, entrada: Explicacion(texto="Explicación de prueba.",
                                                              tono="positivo", nivel_importancia=3))

    texto = rc.generar_reporte("AAPL")
    assert "Posición #2 de la shortlist de hoy" in texto
    assert "sobre 480 empresas analizadas" in texto  # universo_n viene del archivo real, no fijo
    assert "Explicación de prueba." in texto


def test_ticker_en_shortlist_sin_titulares_no_rompe(monkeypatch, tmp_path):
    _escribir_shortlist(tmp_path, [{"ticker": "MSFT", "score": 80.0, "sector": "Technology", "sub_scores": {}}])
    monkeypatch.setattr(rc, "SHORTLIST_PATH", tmp_path / "shortlist_hoy.json")
    monkeypatch.setattr(rc, "YahooProvider",
                        lambda: _FakeProvider({"MSFT": _barras("MSFT")}, {"MSFT": Fundamentales("MSFT")}))
    monkeypatch.setattr(rc, "titulares_google_news", lambda query, maximo=5: [])

    texto = rc.generar_reporte("MSFT")
    assert "no se encontraron titulares recientes" in texto.lower()


def test_fundamentales_faltantes_dicen_no_disponible(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "SHORTLIST_PATH", tmp_path / "no_existe.json")
    monkeypatch.setattr(rc, "YahooProvider",
                        lambda: _FakeProvider({"JNJ": _barras("JNJ")}, {}))  # sin fundamentales
    monkeypatch.setattr(rc, "titulares_google_news", lambda query, maximo=5: [])

    texto = rc.generar_reporte("JNJ")
    assert "P/E: No disponible" in texto
    assert "Recomendación: No disponible" in texto
    assert "% en manos institucionales: No disponible" in texto


def test_shortlist_entry_tolera_archivo_inexistente(tmp_path):
    original = rc.SHORTLIST_PATH
    try:
        rc.SHORTLIST_PATH = tmp_path / "no_existe.json"
        resultado = rc._shortlist_entry("AAPL")
    finally:
        rc.SHORTLIST_PATH = original
    assert resultado is None


def test_shortlist_entry_tolera_json_corrupto(tmp_path):
    archivo = tmp_path / "shortlist_hoy.json"
    archivo.write_text("esto no es json valido {{{")
    original = rc.SHORTLIST_PATH
    try:
        rc.SHORTLIST_PATH = archivo
        assert rc._shortlist_entry("AAPL") is None
    finally:
        rc.SHORTLIST_PATH = original
