"""Schemas for fitted-model endpoints."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class ModelSummary(BaseModel):
    """Compact view: list page."""

    model_config = ConfigDict(from_attributes=True)

    ticker: str
    fitted_at: datetime
    training_start: date
    training_end: date
    n_obs: int
    predictor_ids: list[str]
    r2: float
    r2_adj: float
    durbin_watson: float
    breusch_pagan_p: float
    max_vif: float
    status: str
    is_active: bool


class ModelDetail(ModelSummary):
    """Detail view adds intercept + coefficients + a human-readable equation."""

    intercept: float
    coefficients: dict[str, float]
    equation: str


class RefitRequest(BaseModel):
    """Optional knobs for an admin-triggered refit."""

    lookback_days: int = 540
    k_per_stock: int = 4
    lag_days: int = 1


class RefitOutcomeOut(BaseModel):
    ticker: str
    status: str
    r2: float | None = None
    n_obs: int | None = None
    predictor_ids: list[str] = []
    error: str | None = None
