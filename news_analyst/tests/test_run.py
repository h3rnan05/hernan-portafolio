"""Pruebas del orquestador: el tope de explicaciones por corrida se
respeta, y la recolección de titulares deduplica dentro de la misma
corrida. Todo con red y LLM mockeados."""

from __future__ import annotations

import news_analyst.run as run
from news_analyst.models import EntradaShortlist


def _shortlist():
    return [
        EntradaShortlist(ticker="AAPL", posicion=1, score=90.0, sector="Technology",
                         nombre="Apple Inc.", industria=None, sub_scores={}),
        EntradaShortlist(ticker="MSFT", posicion=2, score=85.0, sector="Technology",
                         nombre="Microsoft Corp.", industria=None, sub_scores={}),
    ]


def test_analizar_respeta_el_tope_de_explicaciones(monkeypatch):
    monkeypatch.setattr(run, "MAX_EXPLICACIONES", 1)
    llamadas = []

    def _fake_generar(titular, entrada):
        llamadas.append(titular)
        return None  # el contenido no importa para esta prueba

    monkeypatch.setattr(run, "generar_explicacion", _fake_generar)

    titulares = ["Apple anuncia un producto nuevo", "Microsoft lanza una actualización",
                "Noticia sin relación con nadie de la shortlist"]
    relevantes = run.analizar(_shortlist(), titulares)

    assert len(relevantes) == 2  # Apple y Microsoft matchean; la tercera no
    assert len(llamadas) == 1    # pero solo se llamó al LLM una vez (tope=1)


def test_analizar_ignora_titulares_sin_mencion():
    relevantes = run.analizar(_shortlist(), ["Tesla presenta su nuevo modelo"])
    assert relevantes == []


def test_recolectar_titulares_deduplica_entre_consultas(monkeypatch):
    def _fake_titulares(query, maximo=6):
        return ["Titular repetido", f"Titular único de {query}"]

    monkeypatch.setattr(run, "titulares_google_news", _fake_titulares)
    titulares = run.recolectar_titulares(_shortlist())
    assert titulares.count("Titular repetido") == 1
