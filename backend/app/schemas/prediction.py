"""Schemas for prediction endpoints."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class PredictionPoint(BaseModel):
    """One row of the predictions table, frontend-shaped."""

    model_config = ConfigDict(from_attributes=True)

    predicted_for: date
    predicted_at: datetime
    predicted_return: float | None = None
    predicted_price: float
    actual_price: float | None = None
    abs_error_pct: float | None = None


class TickerPredictions(BaseModel):
    ticker: str
    points: list[PredictionPoint]
    mape: float | None = None  # mean abs % error over the returned points


class PortfolioPredictionPoint(BaseModel):
    predicted_for: date
    predicted_value: float
    actual_value: float | None = None
    error_pct: float | None = None


class PortfolioPredictions(BaseModel):
    portfolio_id: str
    points: list[PortfolioPredictionPoint]
    mape: float | None = None


class SimulateRequest(BaseModel):
    """Caller supplies one lagged-return value per predictor; we run every active
    model. Missing predictors fall back to zero.
    """

    inputs: dict[str, float] = Field(
        default_factory=dict,
        description="lagged returns per predictor id, e.g. {'CPI_YoY_US': 0.001}",
    )
    horizon_days: int = Field(default=7, ge=1, le=30)


class SimulatedTicker(BaseModel):
    ticker: str
    predicted_return: float
    predicted_price: float
    last_price: float
    contributions: dict[str, float]


class SimulateResponse(BaseModel):
    inputs: dict[str, float]
    horizon_days: int
    per_ticker: list[SimulatedTicker]
    portfolio_value: float
    portfolio_value_baseline: float
    delta: float
