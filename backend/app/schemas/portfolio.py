"""Schemas for portfolio + position endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PortfolioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None = None
    weights: dict[str, float]
    generated_at: datetime
    mape_30d: float | None = None  # filled when prediction history exists


class PortfolioSnapshotOut(BaseModel):
    """One historical snapshot of a portfolio's weights."""

    model_config = ConfigDict(from_attributes=True)

    portfolio_id: str
    weights: dict[str, float]
    mape_30d: float | None = None
    snapshotted_at: datetime


class HoldingProjection(BaseModel):
    """Per-ticker comparison of user's holding vs model prediction."""

    ticker: str
    quantity: float
    avg_price: float
    last_price: float
    market_value: float
    open_pnl: float
    open_pnl_pct: float
    predicted_price: float | None = None
    predicted_market_value: float | None = None
    predicted_pnl_delta: float | None = None  # predicted_mv - current_mv


class HoldingsProjectionOut(BaseModel):
    """Roll-up of user holdings against current predictions."""

    rows: list[HoldingProjection]
    current_market_value: float
    projected_market_value: float
    projected_delta: float
    projected_delta_pct: float | None = None


class PositionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    snapshot_at: datetime
    account_id: str
    ticker: str
    quantity: float
    avg_price: float
    last_price: float
    market_value: float
    open_pnl: float
    open_pnl_pct: float


class PositionsSyncOut(BaseModel):
    snapshot_count: int
    snapshot_at: datetime
