"""Contratos de entrada/salida del Portfolio Optimizer.

Estos modelos son la interfaz estable: cualquier OptimizationStrategy nueva
(Mean-Variance, Risk Parity, Black-Litterman, HRP, Equal Risk Contribution,
Minimum Variance...) recibe y devuelve exactamente estos tipos. Cambiar el
algoritmo no debe requerir cambiar estos modelos.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    """Un candidato a entrar al portafolio, con todo lo que el optimizador
    necesita para decidir SI entra y con QUÉ PESO. No es una señal de compra
    (esa distinción es del Research Agent) — es solo el input del optimizador."""

    ticker: str
    sector: str
    expected_return: float  # retorno esperado anualizado, ej. 0.12 = 12%
    volatilidad: float  # volatilidad anualizada, ej. 0.25 = 25%
    beta: float
    liquidez_usd: float  # dollar volume promedio diario
    quality_score: float = 50.0  # 0-100, mismo formato que screener/scoring.py
    momentum_score: float = 50.0
    growth_score: float = 50.0
    value_score: float = 50.0
    industry: str | None = None
    market_cap: float | None = None

    def score_factorial(self) -> float:
        """Promedio simple de los 4 factores. Es una tasa de calidad, no de
        retorno — se usa para desempatar entre candidatos con Sharpe similar,
        nunca para rankear por sí solo (eso violaría "nunca maximizar retorno solo")."""
        factores = self.quality_score + self.momentum_score + self.growth_score + self.value_score
        return factores / 4.0


@dataclass(frozen=True)
class Holding:
    """Una posición que YA existe en la cartera actual. Es input (contexto),
    no algo que el optimizador decide — el optimizador solo decide qué
    candidatos NUEVOS agregar alrededor de esto."""

    ticker: str
    sector: str
    peso: float  # % del equity actual
    beta: float
    volatilidad: float


@dataclass(frozen=True)
class Allocation:
    """Una posición en el portafolio resultante, nueva o preexistente."""

    ticker: str
    sector: str
    peso: float
    retorno_esperado: float  # 0.0 para holdings preexistentes sin forecast
    nueva: bool  # True si la agregó esta corrida del optimizador


@dataclass(frozen=True)
class CandidatoRechazado:
    ticker: str
    motivo: str


@dataclass(frozen=True)
class PortafolioOptimo:
    """Salida completa del optimizador — nunca solo pesos: siempre viene con
    las métricas de riesgo que los justifican y los rechazos con motivo,
    para que la decisión sea auditable, no una caja negra."""

    asignaciones: list[Allocation]
    peso_cash: float
    retorno_esperado: float
    volatilidad_esperada: float
    beta_esperado: float
    exposicion_sectorial: dict[str, float]
    score_diversificacion: float
    rechazados: list[CandidatoRechazado]
    resumen_riesgo: str
