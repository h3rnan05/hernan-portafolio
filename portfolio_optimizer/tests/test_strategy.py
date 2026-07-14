"""Pruebas de ScoreWeightedGreedy — portafolios sintéticos, sin red. Cada
regla se aísla relajando las demás, siguiendo el mismo patrón que
risk_manager/tests/test_engine.py."""

from __future__ import annotations

import pytest

from portfolio_optimizer.config import OptimizerConstraints
from portfolio_optimizer.models import Candidate, Holding
from portfolio_optimizer.strategies.score_weighted import ScoreWeightedGreedy


def _c(ticker, sector, expected_return, vol, beta=1.0, quality=50, momentum=50, growth=50, value=50, liquidez_usd=1e9):
    return Candidate(
        ticker=ticker, sector=sector, expected_return=expected_return, volatilidad=vol, beta=beta,
        liquidez_usd=liquidez_usd, quality_score=quality, momentum_score=momentum, growth_score=growth, value_score=value,
    )


def _construir(candidatos, cartera_actual=None, correlaciones=None, **overrides):
    L = OptimizerConstraints(**overrides)
    return ScoreWeightedGreedy().construir(candidatos, cartera_actual or [], correlaciones or {}, L)


# ------------------------- caso feliz -------------------------

def test_llena_la_cartera_respetando_la_reserva_de_cash():
    candidatos = [
        _c("AAPL", "Technology", 0.15, 0.20, 1.2, 80, 70, 75, 60),
        _c("MSFT", "Technology", 0.12, 0.18, 1.1, 85, 65, 70, 55),
        _c("PG", "Consumer Staples", 0.07, 0.10, 0.5, 88, 45, 35, 75),
        _c("JNJ", "Healthcare", 0.08, 0.12, 0.6, 90, 50, 40, 70),
        _c("XOM", "Energy", 0.10, 0.22, 0.9, 60, 55, 45, 80),
    ]
    r = _construir(candidatos)
    assert len(r.asignaciones) == 5
    assert r.rechazados == []
    assert r.peso_cash == pytest.approx(0.15, abs=1e-6)
    assert r.beta_esperado <= 1.3
    assert r.volatilidad_esperada <= 0.25


def test_prefiere_retorno_ajustado_por_riesgo_sobre_retorno_crudo():
    """El optimizador NUNCA debe maximizar solo el retorno esperado."""
    alto_retorno_riesgoso = _c("A", "Technology", 0.30, 0.50, quality=30, momentum=30, growth=30, value=30)
    mejor_ajustado = _c("B", "Technology", 0.10, 0.10, quality=70, momentum=70, growth=70, value=70)
    r = _construir([alto_retorno_riesgoso, mejor_ajustado], max_posiciones=1)
    tickers_elegidos = {a.ticker for a in r.asignaciones if a.nueva}
    assert tickers_elegidos == {"B"}
    assert any(rc.ticker == "A" for rc in r.rechazados)


# ------------------------- vetos, uno por regla -------------------------

def test_veto_max_posiciones():
    candidatos = [
        _c("A", "Technology", 0.10, 0.10),
        _c("B", "Healthcare", 0.10, 0.10),
        _c("C", "Energy", 0.10, 0.10),
    ]
    r = _construir(candidatos, max_posiciones=2, min_cash_pct=0.0)
    assert len([a for a in r.asignaciones if a.nueva]) == 2
    assert len(r.rechazados) == 1
    assert "máximo de posiciones" in r.rechazados[0].motivo


def test_veto_max_sector():
    candidatos = [
        _c("A", "Technology", 0.15, 0.10),
        _c("B", "Technology", 0.10, 0.10),
    ]
    r = _construir(candidatos, max_sector_pct=0.10, min_cash_pct=0.0, max_posicion_pct=1.0)
    nuevas = {a.ticker: a.peso for a in r.asignaciones if a.nueva}
    assert nuevas == {"A": pytest.approx(0.10)}
    assert len(r.rechazados) == 1
    assert "B" == r.rechazados[0].ticker
    assert "sector" in r.rechazados[0].motivo.lower()


def test_veto_correlacion_excesiva():
    a = _c("A", "Technology", 0.15, 0.10, beta=0.5)
    b = _c("B", "Healthcare", 0.10, 0.10, beta=0.5)
    r = _construir(
        [a, b], correlaciones={("A", "B"): 0.9},
        min_cash_pct=0.0, max_posicion_pct=0.40, max_sector_pct=1.0, max_correlacion_par=0.70,
    )
    nuevas = {alloc.ticker for alloc in r.asignaciones if alloc.nueva}
    assert nuevas == {"A"}
    rechazo = next(rc for rc in r.rechazados if rc.ticker == "B")
    assert "correlación excesiva" in rechazo.motivo


def test_sin_correlacion_informada_no_rechaza():
    a = _c("A", "Technology", 0.15, 0.10, beta=0.5)
    b = _c("B", "Healthcare", 0.10, 0.10, beta=0.5)
    r = _construir([a, b], min_cash_pct=0.0, max_posicion_pct=0.40, max_sector_pct=1.0)
    nuevas = {alloc.ticker for alloc in r.asignaciones if alloc.nueva}
    assert nuevas == {"A", "B"}


def test_reduce_peso_en_vez_de_rechazar_por_volatilidad_maxima():
    candidato = _c("X", "Technology", 0.15, 0.30, beta=0.5)
    r = _construir([candidato], max_vol_portafolio=0.05, min_cash_pct=0.0)
    nueva = next(a for a in r.asignaciones if a.nueva)
    assert 0.0 < nueva.peso < 0.25  # se redujo del tentativo (0.25), no se rechazó
    assert r.volatilidad_esperada <= 0.05 + 1e-9


def test_rechaza_si_ni_el_tamano_minimo_respeta_el_beta_maximo():
    candidato = _c("X", "Technology", 0.15, 0.10, beta=1.5)
    r = _construir([candidato], max_beta_portafolio=0.01, min_cash_pct=0.0)
    assert r.asignaciones == []
    assert len(r.rechazados) == 1
    assert "beta" in r.rechazados[0].motivo.lower() or "volatilidad" in r.rechazados[0].motivo.lower()


def test_no_promedia_posicion_existente():
    holding = [Holding(ticker="AAPL", sector="Technology", peso=0.20, beta=1.2, volatilidad=0.20)]
    candidato = _c("AAPL", "Technology", 0.15, 0.20, beta=1.2)
    r = _construir([candidato], cartera_actual=holding)
    assert any(a.ticker == "AAPL" and not a.nueva for a in r.asignaciones)
    assert len(r.rechazados) == 1
    assert "promedia" in r.rechazados[0].motivo


def test_no_alcanza_presupuesto_invertible():
    holdings = [Holding(ticker=f"H{i}", sector="Healthcare", peso=0.17, beta=1.0, volatilidad=0.10) for i in range(5)]
    candidato = _c("X", "Technology", 0.15, 0.10)
    r = _construir([candidato], cartera_actual=holdings, max_posiciones=10, max_sector_pct=1.0)
    # 5 * 0.17 = 0.85 ya invertido, min_cash_pct=0.15 default -> presupuesto = 1 - 0.15 - 0.85 = 0.0
    assert len(r.rechazados) == 1
    assert "presupuesto" in r.rechazados[0].motivo


# ------------------------- diversificación -------------------------

def test_diversificacion_score_positivo_con_posiciones_no_correlacionadas():
    candidatos = [
        _c("A", "Technology", 0.12, 0.15),
        _c("B", "Healthcare", 0.10, 0.12),
    ]
    r = _construir(candidatos, min_cash_pct=0.0, max_posicion_pct=0.50, max_sector_pct=1.0)
    assert r.score_diversificacion > 0.0
