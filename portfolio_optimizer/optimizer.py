"""PortfolioOptimizer — punto de entrada único del módulo.

No implementa ningún algoritmo de optimización él mismo: valida las
restricciones y delega en una OptimizationStrategy intercambiable. Esto es
lo que permite, a futuro, reemplazar el algoritmo (Mean-Variance, Risk
Parity, Black-Litterman, HRP, Equal Risk Contribution, Minimum Variance)
sin tocar este archivo ni los modelos de entrada/salida — se implementa la
interfaz `OptimizationStrategy` y se inyecta acá.
"""

from __future__ import annotations

from portfolio_optimizer.config import OptimizerConstraints
from portfolio_optimizer.models import Candidate, Holding, PortafolioOptimo
from portfolio_optimizer.strategies.base import OptimizationStrategy
from portfolio_optimizer.strategies.score_weighted import ScoreWeightedGreedy


class PortfolioOptimizer:
    def __init__(
        self, restricciones: OptimizerConstraints, estrategia: OptimizationStrategy | None = None,
    ) -> None:
        restricciones.validar()
        self.restricciones = restricciones
        self.estrategia = estrategia or ScoreWeightedGreedy()

    def construir_portafolio(
        self,
        candidatos: list[Candidate],
        cartera_actual: list[Holding] | None = None,
        correlaciones: dict[tuple[str, str], float] | None = None,
    ) -> PortafolioOptimo:
        return self.estrategia.construir(
            candidatos=candidatos,
            cartera_actual=cartera_actual or [],
            correlaciones=correlaciones or {},
            restricciones=self.restricciones,
        )
