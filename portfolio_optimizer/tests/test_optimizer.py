"""Pruebas del orquestador PortfolioOptimizer: validación de config y
delegación a la estrategia inyectada — no reimplementa las pruebas de
reglas (esas viven en test_strategy.py)."""

from __future__ import annotations

import pytest

from portfolio_optimizer.config import OptimizerConstraints
from portfolio_optimizer.models import Allocation, Candidate, CandidatoRechazado, Holding, PortafolioOptimo
from portfolio_optimizer.optimizer import PortfolioOptimizer
from portfolio_optimizer.strategies.base import OptimizationStrategy
from portfolio_optimizer.strategies.score_weighted import ScoreWeightedGreedy


def test_limites_invalidos_explotan_temprano():
    with pytest.raises(ValueError):
        PortfolioOptimizer(OptimizerConstraints(max_posiciones=0))
    with pytest.raises(ValueError):
        PortfolioOptimizer(OptimizerConstraints(max_sector_pct=1.5))


def test_usa_score_weighted_greedy_por_defecto():
    opt = PortfolioOptimizer(OptimizerConstraints())
    assert isinstance(opt.estrategia, ScoreWeightedGreedy)


def test_delega_en_la_estrategia_inyectada():
    """Prueba directa de que el algoritmo es intercambiable: una estrategia
    falsa reemplaza a ScoreWeightedGreedy sin que PortfolioOptimizer cambie."""

    class EstrategiaFalsa(OptimizationStrategy):
        def construir(self, candidatos, cartera_actual, correlaciones, restricciones):
            return PortafolioOptimo(
                asignaciones=[Allocation("X", "Technology", 1.0, 0.0, True)],
                peso_cash=0.0,
                retorno_esperado=0.0,
                volatilidad_esperada=0.0,
                beta_esperado=0.0,
                exposicion_sectorial={},
                score_diversificacion=0.0,
                rechazados=[CandidatoRechazado("Y", "estrategia falsa de prueba")],
                resumen_riesgo="falso",
            )

    opt = PortfolioOptimizer(OptimizerConstraints(), estrategia=EstrategiaFalsa())
    r = opt.construir_portafolio(candidatos=[], cartera_actual=[], correlaciones={})
    assert r.resumen_riesgo == "falso"
    assert r.asignaciones[0].ticker == "X"


def test_construir_portafolio_acepta_inputs_opcionales_vacios():
    opt = PortfolioOptimizer(OptimizerConstraints())
    candidato = Candidate(
        ticker="AAPL", sector="Technology", expected_return=0.12, volatilidad=0.20, beta=1.0, liquidez_usd=1e9,
    )
    r = opt.construir_portafolio([candidato])
    assert isinstance(r, PortafolioOptimo)
    assert isinstance(r.asignaciones, list)


def test_cartera_actual_se_respeta_como_contexto():
    opt = PortfolioOptimizer(OptimizerConstraints())
    holding = Holding(ticker="MSFT", sector="Technology", peso=0.30, beta=1.1, volatilidad=0.18)
    r = opt.construir_portafolio(candidatos=[], cartera_actual=[holding])
    assert any(a.ticker == "MSFT" and not a.nueva for a in r.asignaciones)
