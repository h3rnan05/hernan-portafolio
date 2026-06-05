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
    resid_std: float | None = None
    estimator: str = "ols"
    alpha: float | None = None
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
    k_per_stock: int = 3
    lag_days: int = 1
    estimator: str = "ols"  # 'ols' | 'ridge' | 'lasso' (HER-16)
    alpha: float | None = None  # fixed regularization; None → CV
    allow_reuse: bool = True  # HER-14


class RefitOutcomeOut(BaseModel):
    ticker: str
    status: str
    r2: float | None = None
    n_obs: int | None = None
    predictor_ids: list[str] = []
    error: str | None = None


class ValidationCurvePoint(BaseModel):
    """One day of the cumulative-return comparison."""

    on: date
    strategy: float
    buy_hold: float


class ValidationResult(BaseModel):
    """Out-of-sample walk-forward metrics for one ticker (HER-13)."""

    ticker: str
    estimator: str
    train_window: int
    step: int
    n_windows: int
    n_predictions: int
    hit_rate: float | None = None
    up_day_base_rate: float | None = None
    edge_vs_base: float | None = None
    hit_rate_pvalue: float | None = None
    significant: bool = False
    rmse: float | None = None
    mae: float | None = None
    sharpe_strategy: float | None = None
    sharpe_buy_hold: float | None = None
    total_return_strategy: float | None = None
    total_return_buy_hold: float | None = None
    cost_bps: float
    curve: list[ValidationCurvePoint] = []
    note: str | None = None


class ObservationAudit(BaseModel):
    """One row of the raw training input, as stored in the observations table."""

    variable_id: str
    observed_on: date
    value: float
    served_by_provider: str | None = None


class ModelAudit(BaseModel):
    """Unrounded model row + every observation that fed the fit.

    Built for human review: coefficients and intercept are emitted as full
    double-precision floats (no formatting) so the auditor can reconstruct
    the regression independently. Diagnostic stats are returned as stored
    (Numeric(10,6) on the way in, lossless on the way out).
    """

    model_id: str
    ticker: str
    fitted_at: datetime
    training_start: date
    training_end: date
    n_obs: int
    predictor_ids: list[str]
    intercept: float
    coefficients: dict[str, float]
    r2: float
    r2_adj: float
    durbin_watson: float
    breusch_pagan_p: float
    max_vif: float
    status: str
    is_active: bool
    observations: list[ObservationAudit]
    observation_count: int
