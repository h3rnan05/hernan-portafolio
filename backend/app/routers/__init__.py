"""FastAPI routers."""

from app.routers import (
    health,
    models,
    observations,
    portfolios,
    positions,
    predictions,
    variables,
)

__all__ = [
    "health",
    "models",
    "observations",
    "portfolios",
    "positions",
    "predictions",
    "variables",
]
