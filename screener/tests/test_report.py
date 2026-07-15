"""Pruebas del reporte en voz de analista: nunca debe filtrar jerga
cuantitativa cruda (nombres de factor, números de sub-score) y debe
traducir los sub-scores a frases en español llano."""

from __future__ import annotations

import re

from screener.config import ScreenerConfig
from screener.options_ideas import DatosOpciones
from screener.report import (
    _nombre_display,
    checklist_investigacion,
    markdown,
    razones,
    resumen_modelo,
    texto_telegram,
)
from screener.scoring import Puntuacion


def _p(ticker, score, sub, sector=None, nombre=None, industria=None,
      tendencia_cruda=None, vol_historica_anual=None, precio_actual=None):
    return Puntuacion(ticker=ticker, score_total=score, sub=sub, sector=sector,
                      nombre=nombre, industria=industria,
                      tendencia_cruda=tendencia_cruda,
                      vol_historica_anual=vol_historica_anual,
                      precio_actual=precio_actual)


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


def test_checklist_es_solo_preguntas_nunca_respuestas():
    preguntas = checklist_investigacion(_p("AAPL", 81, {}, nombre="Apple Inc."))
    assert len(preguntas) >= 10
    assert all(p.strip().endswith("?") for p in preguntas)


def test_checklist_menciona_el_nombre_de_la_empresa():
    preguntas = checklist_investigacion(_p("AAPL", 81, {}, nombre="Apple Inc."))
    assert any("Apple Inc." in p for p in preguntas)


def test_checklist_competidores_incluye_industria_si_se_conoce():
    con_industria = checklist_investigacion(
        _p("AAPL", 81, {}, nombre="Apple Inc.", industria="Consumer Electronics")
    )
    sin_industria = checklist_investigacion(_p("X", 50, {}, nombre="X Corp"))
    competidores_con = next(p for p in con_industria if "competidores" in p)
    competidores_sin = next(p for p in sin_industria if "competidores" in p)
    assert "Consumer Electronics" in competidores_con
    assert "Consumer Electronics" not in competidores_sin


def test_texto_telegram_incluye_checklist_de_investigacion():
    ranking = [_p("AAPL", 81, {"momentum": 90}, nombre="Apple Inc.", industria="Tech")]
    txt = texto_telegram(ranking, ScreenerConfig(), universo_n=480)
    assert "¿Qué deberías investigar?" in txt
    assert "☐" in txt
    assert "¿Cuánta deuda tiene la empresa?" in txt


def test_markdown_incluye_checklist_como_tareas():
    ranking = [_p("AAPL", 81, {"momentum": 90}, nombre="Apple Inc.")]
    md = markdown(ranking, ScreenerConfig(), universo_n=480)
    assert "**¿Qué deberías investigar?**" in md
    assert "- [ ] " in md


def test_texto_telegram_incluye_ideas_de_opciones_sin_datos():
    """Sin proveedor de opciones inyectado, la sección igual aparece,
    honesta: IV/movimiento esperado 'No disponible', ambas ramas de
    estrategia porque no se puede clasificar el nivel de IV."""
    ranking = [_p("AAPL", 81, {"momentum": 90}, nombre="Apple Inc.",
                  tendencia_cruda=3.0, vol_historica_anual=0.20, precio_actual=200.0)]
    txt = texto_telegram(ranking, ScreenerConfig(), universo_n=480)
    assert "Ideas de opciones para investigar" in txt
    assert "IV Rank: No disponible" in txt
    assert "Volatilidad implícita actual: No disponible" in txt
    assert "Long Call" in txt and "Covered Call" in txt  # las dos ramas alcistas
    assert "Esto NO es una recomendación." in txt
    assert "punto de partida educativo" in txt


def test_markdown_incluye_ideas_de_opciones_con_datos_reales():
    p = _p("AAPL", 81, {"momentum": 90}, nombre="Apple Inc.",
           tendencia_cruda=3.0, vol_historica_anual=0.20, precio_actual=200.0)
    datos = {"AAPL": DatosOpciones(
        ticker="AAPL", iv_call_atm=0.10, iv_put_atm=0.10,
        strike_call_atm=200.0, prima_call_atm=4.0,
        dias_a_vencimiento=35, vencimiento="2026-08-15",
        proxima_fecha_resultados="2026-07-30",
    )}
    md = markdown([p], ScreenerConfig(), universo_n=480, datos_opciones=datos)
    assert "Long Call" in md
    assert "Covered Call" not in md  # IV baja conocida -> solo una rama
    assert "$400" in md  # prima real: 4.0 * 100
    assert "2026-07-30" in md  # earnings
    assert "**Esto NO es una recomendación." in md


def test_opciones_nunca_fabrica_numeros_para_estrategias_multi_pata():
    p = _p("XOM", 70, {}, nombre="Exxon", tendencia_cruda=3.0,
           vol_historica_anual=0.20, precio_actual=100.0)
    datos = {"XOM": DatosOpciones(ticker="XOM", iv_call_atm=0.40, iv_put_atm=0.40)}
    txt = texto_telegram([p], ScreenerConfig(), universo_n=100, datos_opciones=datos)
    assert "Covered Call" in txt
    # la sección de Covered Call no debe traer un riesgo/ganancia en dólares
    # reales (un "$0" conceptual -- el piso teórico del precio -- sí se permite)
    bloque = txt.split("Estrategia posible a investigar: Covered Call")[1]
    bloque_estrategia = bloque.split("Nota educativa")[0]
    assert not re.search(r"\$[1-9]", bloque_estrategia)


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
