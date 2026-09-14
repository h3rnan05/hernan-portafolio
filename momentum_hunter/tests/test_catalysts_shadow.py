"""Shadow Tanda 1: markdown, CF PRE vs propuesto, producción intacta.
Sin red. El expand de producción es el #121, no este PR."""

from __future__ import annotations

from pathlib import Path

from momentum_hunter.catalysts.detector import CATALYST_KEYWORDS, clasificar_titular
from momentum_hunter.catalysts.shadow import (
    CATALYST_KEYWORDS_PRE_TANDA1,
    WAVE1_DOC,
    WAVE1_VERSION,
    clasificar_sombra,
    cargar_frases_propuestas,
    keywords_propuestos,
)

_PRE_BUYBACK = (
    "share buyback", "repurchase program", "stock buyback", "buyback program",
)
_PRE_EARNINGS = (
    "quarterly results", "earnings results", "beats estimates", "misses estimates",
    "q1 results", "q2 results", "q3 results", "q4 results", "reports revenue of",
)
_TANDA1_BUYBACK = ("buybacks", "share buybacks", "stock buybacks")
_TANDA1_EARNINGS = (
    "upbeat q1", "upbeat q2", "upbeat q3", "upbeat q4",
    "q1 earnings", "q2 earnings", "q3 earnings", "q4 earnings",
)

_CRM = (
    "Salesforce Spent a Record $27.1 Billion on Buybacks in One Quarter. "
    "Here Is Why That Signal Matters."
)
_ORCL = "Oracle shares jump after upbeat Q1"

_DIR = Path(__file__).resolve().parent.parent / "catalysts"
_CURADAS = _DIR / "muestras" / "wave1_curadas.json"


def test_produccion_sigue_siendo_pre_tanda1():
    assert CATALYST_KEYWORDS["buyback"] == _PRE_BUYBACK
    assert CATALYST_KEYWORDS["earnings"] == _PRE_EARNINGS
    assert CATALYST_KEYWORDS_PRE_TANDA1["buyback"] == _PRE_BUYBACK
    assert CATALYST_KEYWORDS_PRE_TANDA1["earnings"] == _PRE_EARNINGS


def test_markdown_tanda1_y_union_shadow():
    extras = cargar_frases_propuestas(WAVE1_DOC)
    assert extras["buyback"] == _TANDA1_BUYBACK
    assert extras["earnings"] == _TANDA1_EARNINGS
    merged = keywords_propuestos()
    assert merged["buyback"] == _PRE_BUYBACK + _TANDA1_BUYBACK
    assert merged["earnings"] == _PRE_EARNINGS + _TANDA1_EARNINGS
    assert WAVE1_VERSION == "tanda1-v1"


def test_loader_falla_sin_marcas(tmp_path):
    p = tmp_path / "vacio.md"
    p.write_text("# hola\n", encoding="utf-8")
    try:
        cargar_frases_propuestas(p)
    except ValueError as ex:
        assert "TANDA1" in str(ex)
    else:
        raise AssertionError("tenía que fallar")


def test_crm_orcl_produccion_no_ve_shadow_si():
    prop = keywords_propuestos()
    assert clasificar_titular(_CRM) is None
    assert clasificar_titular(_ORCL) is None
    m_crm = clasificar_sombra(_CRM, prop)
    m_orcl = clasificar_sombra(_ORCL, prop)
    assert m_crm is not None and m_crm.tipo == "buyback" and m_crm.frase == "buybacks"
    assert m_orcl is not None and m_orcl.tipo == "earnings" and m_orcl.frase == "upbeat q1"


def test_tanda2_no_esta_en_el_markdown_ni_en_produccion():
    extras = cargar_frases_propuestas()
    todas = {f for fs in extras.values() for f in fs}
    for fr in ("reports q1", "reports q2", "beats", "q1:", "share repurchase"):
        assert fr not in todas, fr
        assert fr not in CATALYST_KEYWORDS["buyback"]
        assert fr not in CATALYST_KEYWORDS["earnings"]


def test_sombra_curadas_pre_vs_tanda1():
    import json
    prop = keywords_propuestos()
    data = json.loads(_CURADAS.read_text(encoding="utf-8"))
    for item in data["titulares"]:
        pre = clasificar_sombra(item["titular"], CATALYST_KEYWORDS_PRE_TANDA1)
        post = clasificar_sombra(item["titular"], prop)
        assert (pre.tipo if pre else None) == item["esperado_pre"], item["titular"]
        assert (post.tipo if post else None) == item["esperado_tanda1"], item["titular"]
        if item.get("frase_tanda1"):
            assert post is not None
            assert post.frase == item["frase_tanda1"], item["titular"]


def test_shadow_no_prende_beats_the_market_ni_q1_colon():
    prop = keywords_propuestos()
    trampas = [
        "Zacks: This stock beats the market",
        "Company Q1: revenue watch",
        "Monte Rosa Therapeutics Reports Q2 Loss, Misses Revenue Estimates",
        "No buyback this year, CEO says",
        "Board authorizes a $500 million share repurchase",
    ]
    for tit in trampas:
        assert clasificar_titular(tit) is None, tit
        assert clasificar_sombra(tit, prop) is None, tit


def test_contrafactual_sobre_curadas_delta_tanda1():
    from momentum_hunter.catalysts.contrafactual import (
        cargar_json_titulares,
        evaluar_corpus,
    )
    filas = cargar_json_titulares(_CURADAS, "curada")
    r = evaluar_corpus(
        "curadas", filas,
        CATALYST_KEYWORDS_PRE_TANDA1, keywords_propuestos(),
    )
    assert r.cats_titular_actual == 2
    assert r.cats_titular_propuesto == 5
    assert len(r.nuevos) == 3


def test_markdown_lista_todas_las_frases_tanda1():
    extras = cargar_frases_propuestas()
    doc = WAVE1_DOC.read_text(encoding="utf-8")
    for tipo, frases in extras.items():
        assert f"## {tipo}" in doc, tipo
        for fr in frases:
            assert f"`{fr}`" in doc, fr


def test_contrafactual_reporte_cita_buscador_y_no_flag_40():
    from momentum_hunter.catalysts.contrafactual import construir_reporte
    texto = construir_reporte()
    assert "Sin FLAG" in texto
    assert "5→7" in texto
    assert "55→57" in texto
    assert "shadow" in texto.lower() or "#121" in texto
    assert CATALYST_KEYWORDS["buyback"] == _PRE_BUYBACK


def test_run_detector_ancla_no_importan_shadow():
    import inspect
    from momentum_hunter import run as run_mod
    from momentum_hunter.catalysts import ancla, detector
    for modulo in (run_mod, ancla, detector):
        fuente = inspect.getsource(modulo)
        assert "from momentum_hunter.catalysts.shadow" not in fuente
        assert "WAVE1" not in fuente
        assert "tanda1" not in fuente.lower()
