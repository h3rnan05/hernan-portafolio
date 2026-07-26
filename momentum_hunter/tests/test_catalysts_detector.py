"""Pruebas del detector de catalizadores -- sin red. Verifica que la
clasificación es por keywords (texto plano, no juicio), que la ventana
de días descarta titulares viejos, y sobre todo la regla de Prompt 4
para rumores: solo se confirman con >= `fuentes_minimas_rumor` fuentes
DISTINTAS; el resto de tipos se confirma con un solo titular."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from momentum_hunter.catalysts.detector import (
    Titular,
    YahooNewsProvider,
    clasificar_titular,
    detectar_catalizador,
    minutos_desde_catalizador,
)
from momentum_hunter.config import CONFIG
from momentum_hunter.models import Catalizador

HOY = date(2026, 7, 26)


def test_clasificar_titular_reconoce_fda():
    assert clasificar_titular("Company Receives FDA Approval for New Drug") == "fda"


def test_clasificar_titular_reconoce_earnings():
    assert clasificar_titular("Company Reports Q2 Results, Beats Estimates") == "earnings"


def test_clasificar_titular_sin_match_devuelve_none():
    assert clasificar_titular("Company opens new office downtown") is None


def test_clasificar_titular_prioriza_fda_sobre_earnings_si_ambos_matchean():
    texto = "Company beats estimates and receives FDA approval for new drug"
    assert clasificar_titular(texto) == "fda"


def test_detectar_catalizador_confirma_con_un_solo_titular_no_rumor():
    titulares = [Titular("Company Awarded Contract by US Government", "Reuters", HOY.isoformat())]
    c = detectar_catalizador(titulares, CONFIG, hoy=HOY)
    assert c is not None
    assert c.tipo == "contrato"
    assert c.confirmado is True


def test_detectar_catalizador_rumor_con_una_sola_fuente_se_descarta():
    titulares = [Titular("Company is said to be exploring a sale", "BlogX", HOY.isoformat())]
    assert detectar_catalizador(titulares, CONFIG, hoy=HOY) is None


def test_detectar_catalizador_rumor_confirmado_con_multiples_fuentes():
    titulares = [
        Titular("Company is said to be exploring a sale", "BlogX", HOY.isoformat()),
        Titular("Sources say company exploring strategic options", "Reuters", HOY.isoformat()),
    ]
    c = detectar_catalizador(titulares, CONFIG, hoy=HOY)
    assert c is not None
    assert c.tipo == "rumor"
    assert c.fuentes_adicionales == ("Reuters",) or c.fuentes_adicionales == ("BlogX",)


def test_detectar_catalizador_ignora_titulares_fuera_de_ventana():
    vieja = (HOY - timedelta(days=CONFIG.dias_ventana_catalizador + 5)).isoformat()
    titulares = [Titular("Company Awarded Contract", "Reuters", vieja)]
    assert detectar_catalizador(titulares, CONFIG, hoy=HOY) is None


def test_detectar_catalizador_sin_titulares_devuelve_none():
    assert detectar_catalizador([], CONFIG, hoy=HOY) is None


def test_detectar_catalizador_devuelve_none_sin_match():
    titulares = [Titular("Company opens new office downtown", "Reuters", HOY.isoformat())]
    assert detectar_catalizador(titulares, CONFIG, hoy=HOY) is None


def test_yahoo_news_provider_parsea_formato_plano():
    item = {"title": "Company Awarded Contract", "publisher": "Reuters", "providerPublishTime": 1_700_000_000}
    t = YahooNewsProvider._parsear(item)
    assert t is not None
    assert t.texto == "Company Awarded Contract"
    assert t.fuente == "Reuters"
    assert t.fecha is not None


def test_yahoo_news_provider_parsea_formato_anidado_en_content():
    item = {"content": {"title": "Company Awarded Contract", "provider": {"displayName": "Reuters"},
                        "pubDate": "2026-07-20T10:00:00Z"}}
    t = YahooNewsProvider._parsear(item)
    assert t is not None
    assert t.fuente == "Reuters"
    # Guarda el timestamp COMPLETO (no solo la fecha) -- lo necesita
    # minutos_desde_catalizador para el "hace X minutos" de Prompt 5.
    assert t.fecha == "2026-07-20T10:00:00Z"
    assert t.fecha.startswith("2026-07-20")


def test_minutos_desde_catalizador_con_timestamp_completo():
    hace_18_min = datetime(2026, 7, 26, 13, 42, tzinfo=UTC)
    ahora = datetime(2026, 7, 26, 14, 0, tzinfo=UTC)
    c = Catalizador(tipo="fda", titular="x", fuente="Reuters", fecha=hace_18_min.isoformat())
    assert minutos_desde_catalizador(c, ahora=ahora) == pytest.approx(18.0)


def test_minutos_desde_catalizador_none_sin_hora():
    c = Catalizador(tipo="fda", titular="x", fuente="Reuters", fecha="2026-07-26")
    assert minutos_desde_catalizador(c) is None


def test_minutos_desde_catalizador_none_sin_catalizador():
    assert minutos_desde_catalizador(None) is None


def test_yahoo_news_provider_sin_titulo_devuelve_none():
    assert YahooNewsProvider._parsear({"publisher": "Reuters"}) is None
