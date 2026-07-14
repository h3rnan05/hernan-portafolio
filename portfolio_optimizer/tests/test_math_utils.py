"""Pruebas de la matemática pura de riesgo — con valores calculados a mano."""

from __future__ import annotations

import pytest

from portfolio_optimizer.math_utils import (
    beta_portafolio,
    correlacion,
    hhi,
    score_diversificacion,
    varianza_portafolio,
    volatilidad_portafolio,
)


def test_correlacion_diagonal_es_uno():
    assert correlacion({}, "AAPL", "AAPL") == 1.0


def test_correlacion_busca_ambos_ordenes_del_par():
    m = {("AAPL", "MSFT"): 0.6}
    assert correlacion(m, "AAPL", "MSFT") == 0.6
    assert correlacion(m, "MSFT", "AAPL") == 0.6


def test_correlacion_desconocida_es_cero():
    assert correlacion({}, "AAPL", "XOM") == 0.0


def test_varianza_portafolio_dos_activos_correlacion_conocida():
    # w=[0.5,0.5], sigma=[0.20,0.20], rho=0.5
    # sigma_p^2 = 0.25*0.04 + 0.25*0.04 + 2*0.5*0.5*0.2*0.2*0.5 = 0.03
    pesos = {"A": 0.5, "B": 0.5}
    vols = {"A": 0.20, "B": 0.20}
    corr = {("A", "B"): 0.5}
    assert varianza_portafolio(pesos, vols, corr) == pytest.approx(0.03, abs=1e-9)
    assert volatilidad_portafolio(pesos, vols, corr) == pytest.approx(0.03**0.5, abs=1e-9)


def test_varianza_ignora_pesos_en_cero():
    pesos = {"A": 1.0, "B": 0.0}
    vols = {"A": 0.20, "B": 0.99}
    corr = {}
    assert varianza_portafolio(pesos, vols, corr) == pytest.approx(0.04, abs=1e-9)


def test_beta_portafolio_promedio_ponderado():
    pesos = {"A": 0.6, "B": 0.4}
    betas = {"A": 1.5, "B": 0.5}
    assert beta_portafolio(pesos, betas) == pytest.approx(0.6 * 1.5 + 0.4 * 0.5)


def test_hhi_concentracion_total_vs_diversificada():
    assert hhi({"A": 1.0}) == pytest.approx(1.0)
    assert hhi({"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25}) == pytest.approx(0.25)


def test_score_diversificacion_una_posicion_es_cero():
    assert score_diversificacion({"A": 1.0}, {}) == 0.0


def test_score_diversificacion_sin_posiciones_es_cero():
    assert score_diversificacion({}, {}) == 0.0


def test_score_diversificacion_mejora_con_menos_correlacion():
    pesos = {"A": 0.5, "B": 0.5}
    alta_corr = score_diversificacion(pesos, {("A", "B"): 0.9})
    baja_corr = score_diversificacion(pesos, {("A", "B"): 0.1})
    assert baja_corr > alta_corr
