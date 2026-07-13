"""Tipos de datos del Risk Manager. Sin lógica — solo forma."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TradeCandidate:
    """Una propuesta de compra que llega de un Decision Engine / research
    upstream. El Risk Manager NUNCA decide si esto es una buena idea de
    inversión — solo si el TAMAÑO y el RIESGO son aceptables."""
    ticker: str
    precio: float
    atr: float
    sector: str | None = None


@dataclass(frozen=True)
class Posicion:
    qty: int
    entrada: float
    stop: float
    sector: str | None = None


@dataclass(frozen=True)
class PortfolioState:
    equity: float
    cash: float
    posiciones: dict[str, Posicion] = field(default_factory=dict)

    def valor_posiciones(self, precios_actuales: dict[str, float] | None = None) -> float:
        precios_actuales = precios_actuales or {}
        return sum(
            p.qty * precios_actuales.get(t, p.entrada)
            for t, p in self.posiciones.items()
        )

    def riesgo_abierto_usd(self) -> float:
        """Suma de lo que se pierde si TODOS los stops actuales se tocan."""
        return sum(
            p.qty * max(0.0, p.entrada - p.stop)
            for p in self.posiciones.values()
        )

    def exposicion_sector_usd(self, sector: str) -> float:
        return sum(
            p.qty * p.entrada
            for p in self.posiciones.values()
            if p.sector == sector
        )


@dataclass(frozen=True)
class RiskVerdict:
    """El veredicto del Risk Manager. `aprobado=False` es un VETO — el
    Decision Engine no puede pasar por encima de esto."""
    aprobado: bool
    motivo: str
    ticker: str = ""
    qty: int = 0
    stop: float = 0.0
    riesgo_usd: float = 0.0
    riesgo_pct_equity: float = 0.0
