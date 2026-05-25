"""FastAPI routers."""

from app.routers import (
    accounts,
    health,
    holdings,
    models,
    observations,
    portfolios,
    positions,
    predictions,
    variables,
)

__all__ = [
    "accounts",
    "health",
    "holdings",
    "models",
    "observations",
    "portfolios",
    "positions",
    "predictions",
    "variables",
]
