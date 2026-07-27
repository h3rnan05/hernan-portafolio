"""Pruebas de selección de estrategia -- verifica cada rama de la
decisión determinística (sin red: `opciones_disponibles` se pasa
directamente, nunca se llama a `tiene_opciones` de verdad)."""

from __future__ import annotations

from momentum_hunter.config import CONFIG
from momentum_hunter.strategy import decidir_estrategia


def test_no_operar_si_score_insuficiente():
    nombre, _ = decidir_estrategia("TST", "breakout", 50.0, True, True, CONFIG)
    assert nombre == "No Operar"


def test_no_operar_si_catalizador_no_confirmado():
    nombre, _ = decidir_estrategia("TST", "breakout", 95.0, False, True, CONFIG)
    assert nombre == "No Operar"


def test_comprar_acciones_sin_opciones_disponibles():
    nombre, justificacion = decidir_estrategia("TST", "breakout", 95.0, True, False, CONFIG)
    assert nombre == "Comprar acciones"
    assert justificacion


def test_long_call_para_breakout_con_opciones():
    nombre, _ = decidir_estrategia("TST", "breakout", 95.0, True, True, CONFIG)
    assert nombre == "Long Call"


def test_long_call_para_short_squeeze():
    nombre, _ = decidir_estrategia("TST", "short_squeeze", 95.0, True, True, CONFIG)
    assert nombre == "Long Call"


def test_bull_call_spread_para_trend_continuation():
    nombre, _ = decidir_estrategia("TST", "trend_continuation", 95.0, True, True, CONFIG)
    assert nombre == "Bull Call Spread"


def test_cash_secured_put_para_reversal():
    nombre, _ = decidir_estrategia("TST", "reversal", 95.0, True, True, CONFIG)
    assert nombre == "Cash Secured Put"


def test_justificacion_menciona_el_ticker():
    _, justificacion = decidir_estrategia("ACME", "reversal", 95.0, True, True, CONFIG)
    assert any("ACME" in b for b in justificacion)
