"""FastAPI routers."""

from app.routers import (
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
    "health",
    "holdings",
    "models",
    "observations",
    "portfolios",
    "positions",
    "predictions",
    "variables",
]
