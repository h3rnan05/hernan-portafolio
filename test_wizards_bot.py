"""Pruebas de wizards_bot.py -- sin red, sin estado. Cubre únicamente
`texto_revision_mercado`, la función pura extraída del mensaje diario
(pedido directo del dueño del producto, 2026-08-04: el mensaje completo
-- una línea por cada uno de los 10 tickers del universo, siempre --
resultaba "mucho" y "no está bien organizado"). El resto de wizards_bot.py
(red, estado persistido, espejo Webull, explorador con LLM) no tiene
pruebas todavía -- fuera de alcance de este cambio."""

from __future__ import annotations

from wizards_bot import UMBRAL_CERCA_SENAL, texto_revision_mercado


def _ind(precio: float, max55: float) -> dict:
    return {"precio": precio, "max55": max55, "atr": 1.0, "min20": precio * 0.9}


def test_en_posicion_siempre_se_muestra_completo_con_cantidad():
    inds = {"SPY": _ind(767.82, 700.0)}
    posiciones = {"SPY": {"qty": 1, "entrada": 767.82, "stop": 751.39}}
    lineas = texto_revision_mercado(inds, posiciones)
    assert "En posición:" in lineas
    assert "  SPY: 1 u. | $767.82 | stop $751.39 | P&L $+0.00" in lineas


def test_en_posicion_calcula_pnl_real():
    inds = {"EFA": _ind(107.08, 100.0)}
    posiciones = {"EFA": {"qty": 14, "entrada": 105.54, "stop": 102.90}}
    lineas = texto_revision_mercado(inds, posiciones)
    pnl_esperado = (107.08 - 105.54) * 14
    assert f"P&L ${pnl_esperado:+.2f}" in "\n".join(lineas)


def test_cerca_de_senal_muestra_el_numero_real():
    # a 0.8% de romper el máximo de 55 días
    inds = {"IWM": _ind(300.36, 300.36 * 1.008)}
    lineas = texto_revision_mercado(inds, {})
    assert "Cerca de señal:" in lineas
    assert any("IWM: a 0.8% de señal" in c for c in lineas)


def test_ruptura_sin_comprar_se_marca_con_fuego_en_cerca():
    # precio YA por encima del máximo de 55 días (dist negativa)
    inds = {"XYZ": _ind(110.0, 100.0)}
    lineas = texto_revision_mercado(inds, {})
    assert "Cerca de señal:" in lineas
    assert "  🔥 XYZ: en ruptura" in lineas
    assert "Lejos de señal" not in "\n".join(lineas)


def test_lejos_de_senal_se_comprime_a_solo_tickers():
    inds = {"GLD": _ind(374.76, 374.76 * 1.152), "SLV": _ind(53.90, 53.90 * 1.434)}
    lineas = texto_revision_mercado(inds, {})
    assert "En posición:" not in "\n".join(lineas)
    assert "Cerca de señal:" not in "\n".join(lineas)
    assert "Lejos de señal: GLD, SLV" in lineas
    # nunca se muestra el precio/número individual de un ticker lejano
    assert "374.76" not in "\n".join(lineas)
    assert "53.90" not in "\n".join(lineas)


def test_umbral_cerca_es_frontera_correcta():
    # justo en el umbral (5.0%) NO cuenta como "cerca" -- dist < umbral estricto
    precio = 100.0
    max55_en_umbral = precio * (1 + UMBRAL_CERCA_SENAL / 100)
    inds = {"X": _ind(precio, max55_en_umbral)}
    lineas = texto_revision_mercado(inds, {})
    assert "Lejos de señal: X" in lineas


def test_combina_los_tres_niveles_en_orden():
    inds = {
        "SPY": _ind(767.82, 700.0),          # en posición
        "IWM": _ind(300.36, 300.36 * 1.008),  # cerca
        "GLD": _ind(374.76, 374.76 * 1.152),  # lejos
    }
    posiciones = {"SPY": {"qty": 1, "entrada": 767.82, "stop": 751.39}}
    lineas = texto_revision_mercado(inds, posiciones)
    texto = "\n".join(lineas)
    assert texto.index("En posición:") < texto.index("Cerca de señal:")
    assert texto.index("Cerca de señal:") < texto.index("Lejos de señal:")


def test_universo_vacio_no_rompe():
    lineas = texto_revision_mercado({}, {})
    assert lineas == ["Revisión de mercado (canales 55/20d):"]
