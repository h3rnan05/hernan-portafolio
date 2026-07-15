"""Pruebas del formato de salida: "sin noticias relevantes" es una salida
válida y explícita, nunca silencio; el disclaimer siempre aparece."""

from __future__ import annotations

from news_analyst.formato import DISCLAIMER, texto_telegram
from news_analyst.models import EntradaShortlist, Explicacion, Mencion, NoticiaRelevante


def _entrada(ticker="AAPL", posicion=2, nombre="Apple Inc."):
    return EntradaShortlist(ticker=ticker, posicion=posicion, score=81.0,
                            sector="Technology", nombre=nombre, industria=None, sub_scores={})


def test_sin_relevantes_es_una_salida_explicita_no_silencio():
    txt = texto_telegram([], total_titulares=12)
    assert "ninguno menciona" in txt.lower()
    assert "12" in txt


def test_con_explicacion_muestra_tono_y_estrellas():
    mencion = Mencion(entrada=_entrada(), coincidio_por="nombre")
    explicacion = Explicacion(texto="Apple anunció un producto nuevo.",
                              tono="positivo", nivel_importancia=4)
    r = NoticiaRelevante(titular="Apple anuncia algo", mencion=mencion, explicacion=explicacion)
    txt = texto_telegram([r], total_titulares=5)
    assert "Apple Inc. (AAPL)" in txt
    assert "#2" in txt
    assert "🟢" in txt
    assert "⭐⭐⭐⭐☆" in txt
    assert DISCLAIMER in txt


def test_sin_explicacion_no_inventa_una_razon_especifica():
    mencion = Mencion(entrada=_entrada(), coincidio_por="ticker")
    r = NoticiaRelevante(titular="Apple anuncia algo", mencion=mencion, explicacion=None)
    txt = texto_telegram([r], total_titulares=5)
    assert "sin explicación disponible" in txt.lower()
    assert "1 titular" in txt


def test_disclaimer_siempre_presente_con_o_sin_relevantes():
    assert DISCLAIMER in texto_telegram([], total_titulares=0)
    mencion = Mencion(entrada=_entrada(), coincidio_por="nombre")
    r = NoticiaRelevante(titular="x", mencion=mencion, explicacion=None)
    assert DISCLAIMER in texto_telegram([r], total_titulares=1)
