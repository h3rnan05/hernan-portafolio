"""Pruebas del cruce determinístico titular <-> shortlist. Cero red, cero
LLM -- es exactamente la parte "confiable" del feature."""

from __future__ import annotations

import json

from news_analyst.matching import cargar_shortlist, detectar_mencion
from news_analyst.models import EntradaShortlist


def _entrada(ticker, posicion, nombre=None, score=80.0, sector="Technology"):
    return EntradaShortlist(ticker=ticker, posicion=posicion, score=score,
                            sector=sector, nombre=nombre, industria=None, sub_scores={})


def test_detecta_por_nombre_de_empresa():
    shortlist = [_entrada("AAPL", 2, nombre="Apple Inc.")]
    m = detectar_mencion("Apple anuncia nuevo iPhone con IA integrada", shortlist)
    assert m is not None
    assert m.entrada.ticker == "AAPL"
    assert m.coincidio_por == "nombre"


def test_nombre_corto_quita_sufijos_corporativos():
    shortlist = [_entrada("BAC", 5, nombre="Bank of America Corporation")]
    m = detectar_mencion("Bank of America reporta ganancias trimestrales", shortlist)
    assert m is not None and m.entrada.ticker == "BAC"


def test_detecta_por_ticker_si_no_hay_nombre():
    shortlist = [_entrada("NVDA", 1, nombre=None)]
    m = detectar_mencion("NVDA sube tras resultados", shortlist)
    assert m is not None and m.coincidio_por == "ticker"


def test_ticker_corto_no_genera_falso_positivo():
    """'C' (Citigroup) es demasiado corto para matchear solo -- dispararía
    con cualquier oración que contenga la letra C como palabra."""
    shortlist = [_entrada("C", 3, nombre=None)]
    m = detectar_mencion("The Fed said C could be a factor in rate decisions", shortlist)
    assert m is None


def test_sin_mencion_devuelve_none():
    shortlist = [_entrada("AAPL", 1, nombre="Apple Inc."), _entrada("MSFT", 2, nombre="Microsoft")]
    assert detectar_mencion("Tesla presenta su nuevo modelo eléctrico", shortlist) is None


def test_no_hace_match_parcial_de_palabra():
    """'IBM' no debe matchear dentro de 'IBMX' o palabras que solo la contienen."""
    shortlist = [_entrada("IBM", 4, nombre=None)]
    assert detectar_mencion("IBMXcorp anuncia fusión", shortlist) is None
    assert detectar_mencion("IBM anuncia fusión", shortlist) is not None


def test_prioriza_nombre_sobre_ticker_en_orden_de_shortlist():
    shortlist = [
        _entrada("AAPL", 1, nombre="Apple Inc."),
        _entrada("MSFT", 2, nombre="Microsoft Corp."),
    ]
    # el titular menciona a Microsoft por nombre y a Apple no -- debe encontrar Microsoft
    m = detectar_mencion("Microsoft lanza nueva actualización de Windows", shortlist)
    assert m.entrada.ticker == "MSFT"


def test_cargar_shortlist_desde_json(tmp_path):
    archivo = tmp_path / "shortlist_hoy.json"
    archivo.write_text(json.dumps({
        "fecha": "2026-07-15T12:00:00+00:00",
        "universo_escaneado": 480,
        "shortlist": [
            {"ticker": "AAPL", "score": 81.0, "sector": "Technology",
             "sub_scores": {"momentum": 90}, "nombre": "Apple Inc.", "industria": "Consumer Electronics"},
            {"ticker": "BNY", "score": 82.8, "sector": "Financial Services",
             "sub_scores": {"calidad": 70}},  # versión vieja: sin nombre/industria
        ],
    }))
    shortlist = cargar_shortlist(archivo)
    assert len(shortlist) == 2
    assert shortlist[0].ticker == "AAPL" and shortlist[0].posicion == 1
    assert shortlist[0].nombre == "Apple Inc." and shortlist[0].industria == "Consumer Electronics"
    assert shortlist[1].ticker == "BNY" and shortlist[1].posicion == 2
    assert shortlist[1].nombre is None  # tolerante a archivos viejos
