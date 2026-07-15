"""Pruebas del reporte en voz de analista: nunca debe filtrar jerga
cuantitativa cruda (nombres de factor, números de sub-score) y debe
traducir los sub-scores a frases en español llano."""

from __future__ import annotations

from screener.config import ScreenerConfig
from screener.report import _nombre_display, markdown, razones, resumen_modelo, texto_telegram
from screener.scoring import Puntuacion


def _p(ticker, score, sub, sector=None, nombre=None, industria=None):
    return Puntuacion(ticker=ticker, score_total=score, sub=sub, sector=sector,
                      nombre=nombre, industria=industria)


def test_razones_traduce_a_lenguaje_llano_y_omite_debiles():
    p = _p("AAPL", 81, {"momentum": 90, "calidad": 70, "valor": 40})
    r = razones(p, 3)
    assert len(r) == 2  # valor=40 queda fuera (< 60)
    assert all(not any(ch.isdigit() for ch in frase) for frase in r)


def test_frase_intensidad_segun_score():
    fuerte = razones(_p("A", 90, {"momentum": 90}), 1)[0]
    solida = razones(_p("B", 65, {"momentum": 65}), 1)[0]
    assert fuerte != solida


def test_razones_sin_factores_fuertes_da_lista_vacia():
    p = _p("X", 30, {"momentum": 20, "valor": 10})
    assert razones(p, 3) == []


def test_nombre_display_cae_a_ticker_si_no_hay_nombre():
    assert _nombre_display(_p("AAPL", 80, {}, nombre=None)) == "AAPL"
    assert _nombre_display(_p("AAPL", 80, {}, nombre="Apple Inc.")) == "Apple Inc."


def test_texto_telegram_no_contiene_jerga_cruda():
    ranking = [
        _p("AAPL", 81, {"momentum": 98, "tendencia": 100, "liquidez": 99},
           sector="Technology", nombre="Apple Inc.", industria="Consumer Electronics"),
    ]
    cfg = ScreenerConfig()
    txt = texto_telegram(ranking, cfg, universo_n=480)
    bajo = txt.lower()
    for palabra in ("momentum 9", "tendencia 100", "liquidez 99", "sub-score", "sub_score"):
        assert palabra not in bajo
    assert "no son recomendaciones de compra" in bajo
    assert "Apple Inc. (AAPL)" in txt
    assert "Consumer Electronics" in txt


def test_texto_telegram_industria_cae_a_sector():
    ranking = [_p("XOM", 70, {"calidad": 70}, sector="Energy", industria=None)]
    txt = texto_telegram(ranking, ScreenerConfig(), universo_n=100)
    assert "Energy" in txt


def test_markdown_estructura_por_empresa():
    ranking = [_p("JNJ", 75, {"baja_vol": 88}, sector="Healthcare", nombre="Johnson & Johnson")]
    md = markdown(ranking, ScreenerConfig(), universo_n=200)
    assert "## 1. Johnson & Johnson (JNJ)" in md
    assert "¿Por qué el modelo encontró esta empresa?" in md
    assert "no son recomendaciones de compra" in md.lower()


def test_resumen_modelo_vacio():
    assert "no hubo suficientes" in resumen_modelo([]).lower()


def test_resumen_modelo_refleja_el_factor_dominante():
    top = [
        _p("A", 90, {"momentum": 95, "valor": 40}),
        _p("B", 88, {"momentum": 92, "valor": 35}),
        _p("C", 85, {"momentum": 90, "valor": 30}),
    ]
    resumen = resumen_modelo(top)
    assert "impulso fuerte en el precio" in resumen
    assert "favoreciendo" in resumen
