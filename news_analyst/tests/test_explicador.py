"""Pruebas del explicador: la validación/filtro es pura y se prueba sin
red. generar_explicacion se prueba con la llamada a Claude mockeada -- el
punto es verificar que el LLM NUNCA decide, solo explica, y que si se
desvía la explicación se descarta entera."""

from __future__ import annotations

import json

import pytest

from news_analyst import explicador
from news_analyst.explicador import _parsear, generar_explicacion
from news_analyst.models import EntradaShortlist


def _entrada():
    return EntradaShortlist(ticker="AAPL", posicion=2, score=81.0, sector="Technology",
                            nombre="Apple Inc.", industria="Consumer Electronics",
                            sub_scores={"momentum": 90, "calidad": 70})


def _json_valido(**overrides):
    base = {"explicacion": "Apple anunció un producto nuevo, lo cual refuerza su "
                            "posición en el sector.", "tono": "positivo", "nivel_importancia": 3}
    base.update(overrides)
    return json.dumps(base)


def test_parsear_json_valido():
    e = _parsear(_json_valido())
    assert e is not None
    assert e.tono == "positivo" and e.nivel_importancia == 3


def test_parsear_tolera_json_con_fence_markdown():
    crudo = "```json\n" + _json_valido() + "\n```"
    assert _parsear(crudo) is not None


def test_parsear_rechaza_json_invalido():
    assert _parsear("esto no es json") is None


def test_parsear_rechaza_tono_invalido():
    assert _parsear(_json_valido(tono="alcista")) is None  # no es un tono válido


def test_parsear_rechaza_nivel_fuera_de_rango():
    assert _parsear(_json_valido(nivel_importancia=9)) is None


def test_parsear_rechaza_explicacion_vacia():
    assert _parsear(_json_valido(explicacion="")) is None


@pytest.mark.parametrize("frase", [
    "Deberías comprar más acciones ahora.",
    "Recomiendo vender antes del cierre.",
    "Es momento de comprar en la caída.",
])
def test_parsear_descarta_si_el_llm_sugiere_una_accion(frase):
    """Defensa adicional al prompt: si el LLM se desvía y sugiere una
    acción, la explicación se descarta entera -- nunca llega al usuario."""
    assert _parsear(_json_valido(explicacion=frase)) is None


def test_generar_explicacion_sin_api_key_devuelve_none(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert generar_explicacion("Apple anuncia algo", _entrada()) is None


def test_generar_explicacion_usa_el_resultado_de_llamar_claude(monkeypatch):
    monkeypatch.setattr(explicador, "llamar_claude", lambda system, user: _json_valido())
    e = generar_explicacion("Apple anuncia algo", _entrada())
    assert e is not None and e.tono == "positivo"


def test_generar_explicacion_si_la_llamada_falla_devuelve_none(monkeypatch):
    monkeypatch.setattr(explicador, "llamar_claude", lambda system, user: None)
    assert generar_explicacion("Apple anuncia algo", _entrada()) is None


def test_mensaje_usuario_incluye_contexto_pero_no_inventa_nada():
    """El contexto que se le da al LLM viene solo de la shortlist -- no se
    agrega ningún dato externo que el modelo cuantitativo no calculó."""
    msg = explicador._mensaje_usuario("Apple anuncia algo", _entrada())
    assert "#2" in msg and "AAPL" in msg and "81" in msg


# ------------------------- resumen agregado de noticias -------------------------

def _json_resumen_valido(**overrides):
    base = {"resumen": "La mayoría de las noticias fueron positivas.",
            "puntos": ["Apple sorprendió con buenos resultados.", "Algunos analistas consideran que sigue barata."]}
    base.update(overrides)
    return json.dumps(base)


def test_parsear_resumen_json_valido():
    r = explicador._parsear_resumen(_json_resumen_valido())
    assert r is not None
    assert r.resumen == "La mayoría de las noticias fueron positivas."
    assert len(r.puntos) == 2


def test_parsear_resumen_tolera_json_con_fence_markdown():
    crudo = "```json\n" + _json_resumen_valido() + "\n```"
    assert explicador._parsear_resumen(crudo) is not None


def test_parsear_resumen_rechaza_json_invalido():
    assert explicador._parsear_resumen("esto no es json") is None


def test_parsear_resumen_rechaza_resumen_vacio():
    assert explicador._parsear_resumen(_json_resumen_valido(resumen="")) is None


def test_parsear_resumen_trunca_a_3_puntos():
    r = explicador._parsear_resumen(_json_resumen_valido(
        puntos=["uno", "dos", "tres", "cuatro", "cinco"]))
    assert len(r.puntos) == 3


def test_parsear_resumen_descarta_puntos_vacios():
    r = explicador._parsear_resumen(_json_resumen_valido(puntos=["uno", "", "  "]))
    assert r.puntos == ["uno"]


@pytest.mark.parametrize("frase", [
    "Deberías comprar más acciones ahora.",
    "Recomiendo vender antes del cierre.",
])
def test_parsear_resumen_descarta_si_el_llm_sugiere_una_accion_en_el_resumen(frase):
    assert explicador._parsear_resumen(_json_resumen_valido(resumen=frase)) is None


def test_parsear_resumen_descarta_si_el_llm_sugiere_una_accion_en_un_punto():
    assert explicador._parsear_resumen(
        _json_resumen_valido(puntos=["Es momento de comprar en la caída."])) is None


def test_resumir_noticias_sin_titulares_da_none():
    assert explicador.resumir_noticias([], _entrada()) is None


def test_resumir_noticias_sin_api_key_devuelve_none(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert explicador.resumir_noticias(["Apple anuncia algo"], _entrada()) is None


def test_resumir_noticias_usa_el_resultado_de_llamar_claude(monkeypatch):
    monkeypatch.setattr(explicador, "llamar_claude", lambda system, user: _json_resumen_valido())
    r = explicador.resumir_noticias(["Apple anuncia algo"], _entrada())
    assert r is not None and r.resumen == "La mayoría de las noticias fueron positivas."


def test_resumir_noticias_si_la_llamada_falla_devuelve_none(monkeypatch):
    monkeypatch.setattr(explicador, "llamar_claude", lambda system, user: None)
    assert explicador.resumir_noticias(["Apple anuncia algo"], _entrada()) is None


def test_resumir_noticias_funciona_sin_contexto_de_shortlist(monkeypatch):
    monkeypatch.setattr(explicador, "llamar_claude", lambda system, user: _json_resumen_valido())
    assert explicador.resumir_noticias(["Apple anuncia algo"], None) is not None


def test_mensaje_usuario_resumen_incluye_titulares_y_contexto():
    msg = explicador._mensaje_usuario_resumen(["Titular uno", "Titular dos"], _entrada())
    assert "Titular uno" in msg and "Titular dos" in msg
    assert "#2" in msg and "AAPL" in msg


def test_mensaje_usuario_resumen_sin_entrada_no_rompe():
    msg = explicador._mensaje_usuario_resumen(["Titular uno"], None)
    assert "Titular uno" in msg
