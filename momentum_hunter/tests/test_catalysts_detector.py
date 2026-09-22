"""Pruebas del detector de catalizadores -- sin red. Verifica que la
clasificación es por keywords (texto plano, no juicio), que la ventana
de días descarta titulares viejos, y sobre todo la regla de Prompt 4
para rumores: solo se confirman con >= `fuentes_minimas_rumor` fuentes
DISTINTAS; el resto de tipos se confirma con un solo titular."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from momentum_hunter.catalysts.detector import (
    CATALYST_KEYWORDS,
    Titular,
    YahooNewsProvider,
    clasificar_titular,
    detectar_catalizador,
    minutos_desde_catalizador,
)
from momentum_hunter.catalysts.keyword_rechazos import explicar_rechazos_keyword
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


# --- Parche 2026-09-22: ALKS phase 1b / PoC y LFMD partnership. ---
# No reabre #121 (buybacks, upbeat qN). El matcher sigue siendo substring.


def test_alks_phase_1b_y_proof_of_concept_son_fda_no_sin_keyword():
    # Titular real muestreado el 2026-09-21. "Phase 1b Results" no contiene
    # "phase 2 results" ni "phase 3 results".
    texto = (
        "Alkermes Shares Rise 6.5% After Positive Phase 1b Results "
        "for ADHD Drug ALKS 7290"
    )
    assert clasificar_titular(texto) == "fda"
    titulares = [Titular(texto, "Reuters", "2026-09-21")]
    c = detectar_catalizador(titulares, CONFIG, hoy=date(2026, 9, 21))
    assert c is not None and c.tipo == "fda"
    assert explicar_rechazos_keyword("ALKS", titulares, CONFIG, hoy=date(2026, 9, 21)) == []
    assert clasificar_titular(
        "Alkermes announces positive proof-of-concept data for ADHD candidate"
    ) == "fda"
    assert clasificar_titular(
        "Alkermes announces positive proof of concept data for ADHD candidate"
    ) == "fda"
    assert clasificar_titular("Company posts positive Phase 1 results in ADHD") == "fda"


def test_lfmd_secures_partnership_y_collaboration_son_nuevo_cliente():
    # Titular real muestreado el 2026-09-21. "Secures AT&T Partnership"
    # no contiene "partnership with" ni "strategic partnership".
    texto = (
        "LifeMD (LFMD) Secures AT&T Partnership. "
        "Can Free Memberships Produce Paying Patients?"
    )
    assert clasificar_titular(texto) == "nuevo_cliente"
    titulares = [Titular(texto, "Reuters", "2026-09-21")]
    c = detectar_catalizador(titulares, CONFIG, hoy=date(2026, 9, 21))
    assert c is not None and c.tipo == "nuevo_cliente"
    assert explicar_rechazos_keyword("LFMD", titulares, CONFIG, hoy=date(2026, 9, 21)) == []
    assert clasificar_titular(
        "LifeMD announces strategic collaboration with AT&T on virtual care"
    ) == "nuevo_cliente"
    # Las frases viejas siguen clasificando igual.
    assert clasificar_titular("Company signs agreement with a hospital system") == "nuevo_cliente"
    assert clasificar_titular("Company announces strategic partnership") == "nuevo_cliente"


def test_parche_alks_lfmd_no_reabre_121_ni_otras_frases():
    # Lo que este parche no mete, a propósito.
    assert clasificar_titular("Salesforce Announces $10 Billion Buybacks") is None
    assert clasificar_titular("Oracle Reports Upbeat Q1") is None
    assert clasificar_titular("Boeing Beats Stock Market") is None
    assert clasificar_titular("AT&T Teams Up With LifeMD for Virtual Health Care") is None
    assert clasificar_titular("Company opens new office downtown") is None
    # "phase i" es substring de "phase ii"/"phase iii"; el titular del
    # 22-sep sigue sin keyword. No es el hueco de "Phase 1b".
    assert clasificar_titular(
        "Alkermes Posts Positive Data From Phase I ADHD Study on ALKS 7290"
    ) is None
    todas = {kw for frases in CATALYST_KEYWORDS.values() for kw in frases}
    for prohibida in (
        "buybacks", "upbeat q1", "q1 earnings", "beats", "teams up", "phase i",
    ):
        assert prohibida not in todas
    for frase in (
        "phase 1b", "phase 1", "proof-of-concept", "proof of concept",
        "partnership", "collaboration",
    ):
        assert frase in todas


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
