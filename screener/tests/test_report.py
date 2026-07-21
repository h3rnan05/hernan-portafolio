"""Pruebas del reporte en voz de analista: nunca debe filtrar jerga
cuantitativa cruda (nombres de factor, números de sub-score) y debe
traducir los sub-scores a frases en español llano."""

from __future__ import annotations

import re

from screener.config import ScreenerConfig
from screener.options_ideas import DatosOpciones
from screener.report import (
    DiffShortlist,
    _destacado_del_dia,
    _emoji_sector,
    _flecha_delta,
    _nombre_display,
    calcular_diff,
    checklist_investigacion,
    markdown,
    razones,
    resumen_modelo,
    texto_telegram,
    texto_telegram_corto,
    texto_telegram_lista_completa,
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


def test_texto_telegram_corto_no_incluye_razones_ni_checklist_ni_opciones():
    """El mensaje diario real: nada de investigación profunda, solo el
    aviso de qué pasó el screener."""
    ranking = [
        _p("AAPL", 81, {"momentum": 98, "tendencia": 100, "liquidez": 99},
           sector="Technology", nombre="Apple Inc.", industria="Consumer Electronics"),
    ]
    txt = texto_telegram_corto(ranking, ScreenerConfig(), universo_n=480)
    assert "¿Por qué el modelo encontró esta empresa?" not in txt
    assert "¿Qué deberías investigar?" not in txt
    assert "Ideas de opciones" not in txt
    assert "💻 AAPL (81)" in txt
    assert "Consumer Electronics" in txt
    assert "/report AAPL" in txt
    assert "no es una recomendación de compra" in txt.lower()


def test_texto_telegram_corto_industria_cae_a_sector():
    ranking = [_p("XOM", 70, {}, sector="Energy", industria=None)]
    txt = texto_telegram_corto(ranking, ScreenerConfig(), universo_n=100)
    assert "Energy" in txt


def test_texto_telegram_corto_sin_resultados_no_sugiere_ejemplos():
    txt = texto_telegram_corto([], ScreenerConfig(), universo_n=100)
    assert "/report TICKER" in txt
    assert "Ejemplo:" not in txt


def test_texto_telegram_corto_trunca_al_top_n_mensaje_diario_y_ofrece_list():
    cfg = ScreenerConfig()  # top_n_mensaje_diario=10, top_n=20
    ranking = [_p(f"T{i}", 90 - i, {}) for i in range(15)]
    txt = texto_telegram_corto(ranking, cfg, universo_n=480)
    assert "T0" in txt and "T9" in txt
    assert "T10" not in txt  # truncado, no aparece en el cuerpo
    assert "... y 5 más. Escribe /list" in txt


# ------------------------- diff vs. corrida anterior -------------------------

def _entrada_json(ticker, score, sector="Technology"):
    return {"ticker": ticker, "score": score, "sector": sector, "sub_scores": {}}


def test_calcular_diff_detecta_nuevos_y_salieron():
    anterior = {"shortlist": [_entrada_json("AAPL", 80), _entrada_json("MSFT", 75)]}
    top_hoy = [_p("AAPL", 82, {}), _p("CSX", 70, {})]
    diff = calcular_diff(anterior, top_hoy)
    assert diff.nuevos == ["CSX"]
    assert diff.salieron == ["MSFT"]
    assert diff.deltas == {"AAPL": 2.0}
    assert diff.lider_anterior == "AAPL"  # primera fila de la shortlist anterior


def test_calcular_diff_sin_anterior_no_rompe():
    diff = calcular_diff(None, [_p("AAPL", 80, {})])
    assert diff == DiffShortlist(nuevos=[], salieron=[], deltas={}, lider_anterior=None)


def test_flecha_delta():
    assert _flecha_delta(5) == " ▲5"
    assert _flecha_delta(-3) == " ▼3"
    assert _flecha_delta(0) == " ="
    assert _flecha_delta(0.4) == " ="  # redondea a 0
    assert _flecha_delta(None) == ""


def test_emoji_sector_conocido_y_desconocido():
    assert _emoji_sector("Financial Services") == "🏦"
    assert _emoji_sector("Sector Inventado") == "🏢"
    assert _emoji_sector(None) == "🏢"


def test_texto_telegram_corto_muestra_nuevas_salieron_y_delta():
    diff = DiffShortlist(nuevos=["CSX"], salieron=["JPM"], deltas={"AAPL": 2.0, "BAC": -1.0},
                         lider_anterior="AAPL")
    ranking = [
        _p("AAPL", 82, {}, sector="Technology"),
        _p("BAC", 78, {}, sector="Financial Services"),
        _p("CSX", 70, {}, sector="Industrials"),
    ]
    txt = texto_telegram_corto(ranking, ScreenerConfig(), universo_n=480, diff=diff)
    assert "🟢 Nuevas:" in txt and "CSX" in txt.split("🟢 Nuevas:")[1].split("🔴")[0]
    assert "🔴 Salieron:" in txt and "JPM" in txt
    assert "AAPL (82 ▲2)" in txt
    assert "BAC (78 ▼1)" in txt


def test_texto_telegram_corto_marca_nueva_en_vez_de_flecha_vacia():
    diff = DiffShortlist(nuevos=["CSX"], salieron=[], deltas={}, lider_anterior=None)
    ranking = [_p("CSX", 70, {}, sector="Industrials")]
    txt = texto_telegram_corto(ranking, ScreenerConfig(), universo_n=480, diff=diff)
    assert "🏭 CSX (70 🟢 NUEVA)" in txt


# ------------------------- destacado del día -------------------------

def test_destacado_del_dia_sin_historial_no_inventa():
    ranking = [_p("AAPL", 82, {}, sector="Technology")]
    assert _destacado_del_dia(ranking, None) is None
    assert _destacado_del_dia(ranking, DiffShortlist(nuevos=[], salieron=[], deltas={})) is None


def test_destacado_del_dia_lider_nuevo():
    diff = DiffShortlist(nuevos=["AMD"], salieron=[], deltas={}, lider_anterior="AAPL")
    ranking = [_p("AMD", 79, {}, sector="Technology")]
    texto = _destacado_del_dia(ranking, diff)
    assert "AMD" in texto and "primera vez" in texto and "79" in texto


def test_destacado_del_dia_cambio_de_lider_con_delta():
    diff = DiffShortlist(nuevos=[], salieron=[], deltas={"BNY": 1.0}, lider_anterior="AAPL")
    ranking = [_p("BNY", 84, {}, sector="Financial Services")]
    texto = _destacado_del_dia(ranking, diff)
    assert "BNY" in texto and "primer lugar" in texto and "▲1" in texto


def test_destacado_del_dia_cambio_de_lider_sin_cambio_de_score_no_dice_igual():
    # Regresión: el líder anterior salió de la lista y el nuevo líder tiene
    # el mismo score que tenía antes -- "toma el primer lugar" + "=" leería
    # como contradictorio, así que la flecha de delta se omite.
    diff = DiffShortlist(nuevos=[], salieron=["MSFT"], deltas={"AAPL": 0.0}, lider_anterior="MSFT")
    ranking = [_p("AAPL", 81, {}, sector="Technology")]
    texto = _destacado_del_dia(ranking, diff)
    assert texto == "⭐ Destacado del día: AAPL toma el primer lugar con 81 puntos."
    assert "=" not in texto


def test_destacado_del_dia_lider_sin_cambio_resalta_mayor_subida():
    diff = DiffShortlist(nuevos=[], salieron=[], deltas={"AAPL": 0.5, "BAC": 4.0}, lider_anterior="AAPL")
    ranking = [
        _p("AAPL", 82, {}, sector="Technology"),
        _p("BAC", 78, {}, sector="Financial Services"),
    ]
    texto = _destacado_del_dia(ranking, diff)
    assert "BAC" in texto and "mayor subida" in texto and "▲4" in texto


def test_destacado_del_dia_lider_sin_cambio_ni_subidas_no_destaca_nada():
    diff = DiffShortlist(nuevos=[], salieron=[], deltas={"AAPL": -1.0}, lider_anterior="AAPL")
    ranking = [_p("AAPL", 81, {}, sector="Technology")]
    assert _destacado_del_dia(ranking, diff) is None


def test_texto_telegram_lista_completa_incluye_todas_sin_truncar():
    ranking = [_p(f"T{i}", 90 - i, {}, sector="Technology") for i in range(15)]
    txt = texto_telegram_lista_completa(ranking, universo_n=480)
    assert "T0" in txt and "T14" in txt
    assert "📋 Shortlist completa de hoy (15 de 480 analizadas)" in txt
    assert "/report TICKER" in txt


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
