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
