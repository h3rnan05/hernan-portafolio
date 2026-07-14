"""Contrato que cualquier algoritmo de optimización debe cumplir.

Esto es lo que permite reemplazar el algoritmo (Mean-Variance Optimization,
Risk Parity, Black-Litterman, Hierarchical Risk Parity, Equal Risk
Contribution, Minimum Variance...) sin tocar PortfolioOptimizer ni los
modelos de entrada/salida — solo se escribe una clase nueva que implemente
`construir()` y se inyecta en `PortfolioOptimizer(estrategia=...)`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from portfolio_optimizer.config import OptimizerConstraints
from portfolio_optimizer.models import Candidate, Holding, PortafolioOptimo


class OptimizationStrategy(ABC):
    @abstractmethod
    def construir(
        self,
        candidatos: list[Candidate],
        cartera_actual: list[Holding],
        correlaciones: dict[tuple[str, str], float],
        restricciones: OptimizerConstraints,
    ) -> PortafolioOptimo:
        """Debe ser determinística: mismo input, mismo output, siempre."""
        ...
