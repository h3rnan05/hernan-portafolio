"""Tanda 1: markdown ↔ detector, PRE vs aplicada, trampas de Tanda 2.
Sin red."""

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

_DIR = Path(__file__).resolve().parent.parent / "catalysts"
_CURADAS = _DIR / "muestras" / "wave1_curadas.json"


def test_pre_tanda1_es_el_snapshot_anterior():
    assert CATALYST_KEYWORDS_PRE_TANDA1["buyback"] == _PRE_BUYBACK
    assert CATALYST_KEYWORDS_PRE_TANDA1["earnings"] == _PRE_EARNINGS


def test_produccion_es_pre_mas_tanda1():
    assert CATALYST_KEYWORDS["buyback"] == _PRE_BUYBACK + _TANDA1_BUYBACK
    assert CATALYST_KEYWORDS["earnings"] == _PRE_EARNINGS + _TANDA1_EARNINGS


def test_markdown_tanda1_coincide_con_detector():
    extras = cargar_frases_propuestas(WAVE1_DOC)
    assert extras["buyback"] == _TANDA1_BUYBACK
    assert extras["earnings"] == _TANDA1_EARNINGS
    merged = keywords_propuestos()
    assert merged["buyback"] == CATALYST_KEYWORDS["buyback"]
    assert merged["earnings"] == CATALYST_KEYWORDS["earnings"]
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


def test_loader_ignora_subsecciones_que_no_son_tipo(tmp_path):
    p = tmp_path / "mini.md"
    p.write_text(
        "<!-- TANDA1_INICIO -->\n"
        "## buyback\n"
        "### Variantes raras\n"
        "| frase | x |\n"
        "|---|---|\n"
        "| `buybacks` | near-miss |\n"
        "<!-- TANDA1_FIN -->\n",
        encoding="utf-8",
    )
    extras = cargar_frases_propuestas(p)
    assert extras == {"buyback": ("buybacks",)}


def test_tanda2_no_esta_en_produccion_ni_en_el_markdown():
    extras = cargar_frases_propuestas()
    todas = {f for fs in extras.values() for f in fs}
    prohibidas = {
        "reports q1", "reports q2", "share repurchase", "beats",
        "q1:", "beat estimates", "beats expectations", "tops estimates",
        "buyback",  # singular pelado
    }
    for fr in prohibidas:
        assert fr not in todas, fr
        if fr != "buyback":
            assert fr not in CATALYST_KEYWORDS["buyback"]
            assert fr not in CATALYST_KEYWORDS["earnings"]


def test_pre_no_ve_tanda1_produccion_si():
    crm = (
        "Salesforce Spent a Record $27.1 Billion on Buybacks in One Quarter. "
        "Here Is Why That Signal Matters."
    )
    orcl = "Oracle shares jump after upbeat Q1"
    assert clasificar_sombra(crm, CATALYST_KEYWORDS_PRE_TANDA1) is None
    assert clasificar_sombra(orcl, CATALYST_KEYWORDS_PRE_TANDA1) is None
    assert clasificar_titular(crm) == "buyback"
    assert clasificar_titular(orcl) == "earnings"


def test_sombra_curadas_pre_vs_tanda1():
    import json
    data = json.loads(_CURADAS.read_text(encoding="utf-8"))
    for item in data["titulares"]:
        pre = clasificar_sombra(item["titular"], CATALYST_KEYWORDS_PRE_TANDA1)
        post = clasificar_sombra(item["titular"], CATALYST_KEYWORDS)
        assert (pre.tipo if pre else None) == item["esperado_pre"], item["titular"]
        assert (post.tipo if post else None) == item["esperado_tanda1"], item["titular"]
        if item.get("frase_tanda1"):
            assert post is not None
            assert post.frase == item["frase_tanda1"], item["titular"]


def test_tanda1_no_prende_beats_the_market_ni_q1_colon_ni_reports_qn():
    trampas = [
        "Zacks: This stock beats the market",
        "Stock XYZ beats the S&P 500 this year",
        "Company Q1: revenue watch",
        "Monte Rosa Therapeutics Reports Q2 Loss, Misses Revenue Estimates",
        "No buyback this year, CEO says",
        "The great buyback debate continues",
        "Board authorizes a $500 million share repurchase",
    ]
    for tit in trampas:
        assert clasificar_titular(tit) is None, tit
        assert clasificar_sombra(tit, CATALYST_KEYWORDS) is None, tit


def test_contrafactual_sobre_curadas_delta_tanda1():
    from momentum_hunter.catalysts.contrafactual import (
        cargar_json_titulares,
        evaluar_corpus,
    )
    filas = cargar_json_titulares(_CURADAS, "curada")
    r = evaluar_corpus(
        "curadas", filas,
        CATALYST_KEYWORDS_PRE_TANDA1, CATALYST_KEYWORDS,
    )
    # 3 near-miss Tanda1 + 2 controles PRE + 5 Tanda2/trampas = 10.
    # Cats PRE: 2 controles. Tanda1: 2 + 3 near-miss.
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
    assert CATALYST_KEYWORDS["buyback"] == _PRE_BUYBACK + _TANDA1_BUYBACK


def test_run_y_ancla_no_importan_shadow():
    import inspect
    from momentum_hunter import run as run_mod
    from momentum_hunter.catalysts import ancla, detector
    assert "shadow" not in inspect.getsource(run_mod).lower()
    assert "shadow" not in inspect.getsource(ancla).lower()
    assert "from momentum_hunter.catalysts.shadow" not in inspect.getsource(detector)
    assert "contrafactual" not in inspect.getsource(run_mod)
