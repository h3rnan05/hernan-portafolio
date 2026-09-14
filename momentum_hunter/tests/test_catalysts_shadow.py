"""Shadow Wave 1: loader del markdown, clasificador offline, y la
promesa de que producción no se enteró. Sin red."""

from __future__ import annotations

from pathlib import Path

from momentum_hunter.catalysts.detector import CATALYST_KEYWORDS, clasificar_titular
from momentum_hunter.catalysts.shadow import (
    WAVE1_DOC,
    WAVE1_VERSION,
    clasificar_sombra,
    cargar_frases_propuestas,
    keywords_propuestos,
)

# Congelado a propósito: si alguien "aprovecha" el PR para meter
# frases en detector.py, esto explota. Wave 1 es shadow.
_BUYBACK_PRODUCCION = (
    "share buyback", "repurchase program", "stock buyback", "buyback program",
)
_EARNINGS_PRODUCCION = (
    "quarterly results", "earnings results", "beats estimates", "misses estimates",
    "q1 results", "q2 results", "q3 results", "q4 results", "reports revenue of",
)

_DIR = Path(__file__).resolve().parent.parent / "catalysts"
_CURADAS = _DIR / "muestras" / "wave1_curadas.json"


def test_produccion_buyback_y_earnings_siguen_igual():
    assert CATALYST_KEYWORDS["buyback"] == _BUYBACK_PRODUCCION
    assert CATALYST_KEYWORDS["earnings"] == _EARNINGS_PRODUCCION


def test_clasificar_titular_de_produccion_no_ve_wave1():
    # Huecos de Wave 1: el detector real tiene que seguir diciendo None.
    assert clasificar_titular("Company announces $2B in buybacks") is None
    assert clasificar_titular("Upbeat Q1 lifts shares") is None
    assert clasificar_titular("Profit beats Wall Street estimates") is None
    assert clasificar_titular("Q2 Earnings and Revenues Beat Estimates") is None


def test_loader_lee_solo_el_bloque_propuesto():
    extras = cargar_frases_propuestas(WAVE1_DOC)
    assert set(extras) <= {"buyback", "earnings"}
    assert "buybacks" in extras["buyback"]
    assert "share repurchase" in extras["buyback"]
    assert "upbeat q1" in extras["earnings"]
    assert "reports q2" in extras["earnings"]
    assert "beat estimates" in extras["earnings"]
    assert "beats wall street estimates" in extras["earnings"]
    # Rechazadas: no pueden colarse.
    todas = {f for fs in extras.values() for f in fs}
    assert "q2 earnings" not in todas
    assert "beats the market" not in todas
    assert "buyback" not in todas  # singular pelado
    assert WAVE1_VERSION == "wave1-shadow-v1"


def test_loader_falla_sin_marcas(tmp_path):
    p = tmp_path / "vacio.md"
    p.write_text("# hola\n", encoding="utf-8")
    try:
        cargar_frases_propuestas(p)
    except ValueError as ex:
        assert "WAVE1_PROPUESTAS" in str(ex)
    else:
        raise AssertionError("tenía que fallar")


def test_loader_ignora_subsecciones_que_no_son_tipo(tmp_path):
    p = tmp_path / "mini.md"
    p.write_text(
        "<!-- WAVE1_PROPUESTAS_INICIO -->\n"
        "## buyback\n"
        "### Variantes raras\n"
        "| frase | x |\n"
        "|---|---|\n"
        "| `buybacks` | near-miss |\n"
        "<!-- WAVE1_PROPUESTAS_FIN -->\n",
        encoding="utf-8",
    )
    extras = cargar_frases_propuestas(p)
    assert extras == {"buyback": ("buybacks",)}


def test_keywords_propuestos_no_muta_produccion():
    antes = CATALYST_KEYWORDS["buyback"]
    extras = cargar_frases_propuestas()
    prop = keywords_propuestos(CATALYST_KEYWORDS, extras)
    assert CATALYST_KEYWORDS["buyback"] is antes
    assert CATALYST_KEYWORDS["buyback"] == _BUYBACK_PRODUCCION
    assert "buybacks" in prop["buyback"]
    assert "share buyback" in prop["buyback"]
    # Producción primero: un titular que ya matcheaba sigue reportando
    # la frase vieja, no la nueva.
    m = clasificar_sombra("Coca-Cola stock buybacks accelerate", prop)
    assert m is not None
    assert m.tipo == "buyback"
    assert m.frase == "share buyback" or m.frase == "stock buyback"


def test_sombra_near_miss_y_trampas_de_la_muestra_curada():
    import json
    extras = cargar_frases_propuestas()
    prop = keywords_propuestos(extras=extras)
    data = json.loads(_CURADAS.read_text(encoding="utf-8"))
    for item in data["titulares"]:
        actual = clasificar_sombra(item["titular"], CATALYST_KEYWORDS)
        propuesto = clasificar_sombra(item["titular"], prop)
        exp_a = item["esperado_actual"]
        exp_p = item["esperado_propuesto"]
        assert (actual.tipo if actual else None) == exp_a, item["titular"]
        assert (propuesto.tipo if propuesto else None) == exp_p, item["titular"]
        if item.get("frase_propuesta"):
            assert propuesto is not None
            assert propuesto.frase == item["frase_propuesta"], item["titular"]


def test_sombra_no_prende_beats_the_market_ni_earnings_call():
    extras = cargar_frases_propuestas()
    prop = keywords_propuestos(extras=extras)
    trampas = [
        "Zacks: This stock beats the market",
        "Stock XYZ beats the S&P 500 this year",
        "Q2 Earnings Call Highlights",
        "Should You Play Adobe's Q3 Earnings With ETFs?",
        "Meets Q2 Earnings Estimates",
        "No buyback this year, CEO says",
        "The great buyback debate continues",
    ]
    for tit in trampas:
        assert clasificar_sombra(tit, prop) is None, tit
        assert clasificar_titular(tit) is None, tit


def test_contrafactual_sobre_curadas_tiene_delta_esperado():
    from momentum_hunter.catalysts.contrafactual import (
        cargar_json_titulares,
        evaluar_corpus,
    )
    extras = cargar_frases_propuestas()
    prop = keywords_propuestos(extras=extras)
    filas = cargar_json_titulares(_CURADAS, "curada")
    r = evaluar_corpus("curadas", filas, CATALYST_KEYWORDS, prop)
    # 8 near-miss + 2 controles de producción + 10 trampas = 20.
    # Cats actuales: solo los 2 controles. Propuestos: 2 + 8 near-miss.
    assert r.cats_titular_actual == 2
    assert r.cats_titular_propuesto == 10
    assert len(r.nuevos) == 8
    # Ninguna trampa en los nuevos.
    for _ticker, titular, _tipo, _frase in r.nuevos:
        assert "beats the market" not in titular.lower()
        assert "earnings call highlights" not in titular.lower()


def test_markdown_lista_todas_las_frases_que_carga_el_loader():
    """La lista visible y la que corre el shadow son la misma -- si no,
    el PR miente igual que un alias que está en el código y no en el
    diseño (#118)."""
    extras = cargar_frases_propuestas()
    doc = WAVE1_DOC.read_text(encoding="utf-8")
    for tipo, frases in extras.items():
        assert f"## {tipo}" in doc, tipo
        for fr in frases:
            assert f"`{fr}`" in doc, fr


def test_contrafactual_reporte_no_flag_40_y_produccion_intacta():
    from momentum_hunter.catalysts.contrafactual import construir_reporte
    texto = construir_reporte()
    assert "Sin FLAG" in texto
    assert "CATALYST_KEYWORDS" in texto
    assert CATALYST_KEYWORDS["buyback"] == _BUYBACK_PRODUCCION
    assert CATALYST_KEYWORDS["earnings"] == _EARNINGS_PRODUCCION


def test_run_detector_ancla_no_importan_shadow():
    import inspect
    from momentum_hunter import run as run_mod
    from momentum_hunter.catalysts import ancla, detector
    for modulo in (run_mod, ancla, detector):
        fuente = inspect.getsource(modulo)
        assert "shadow" not in fuente.lower()
        assert "WAVE1" not in fuente
        assert "wave1" not in fuente.lower()
