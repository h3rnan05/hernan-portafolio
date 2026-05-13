"""Holding schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class HoldingIn(BaseModel):
    """Payload to create or update a holding."""

    ticker: str = Field(..., min_length=1, max_length=16)
    quantity: float = Field(..., ge=0)
    avg_price: float = Field(..., ge=0)
    notes: str | None = None


class HoldingUpdate(BaseModel):
    """Partial update — all fields optional."""

    quantity: float | None = Field(None, ge=0)
    avg_price: float | None = Field(None, ge=0)
    notes: str | None = None


class HoldingOut(BaseModel):
    """Holding row enriched with last close + P&L when available."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ticker: str
    quantity: float
    avg_price: float
    notes: str | None = None
    added_at: datetime
    updated_at: datetime
    # Server-computed conveniences:
    last_price: float | None = None
    market_value: float | None = None
    cost_basis: float | None = None
    open_pnl: float | None = None
    open_pnl_pct: float | None = None


class HoldingsSummary(BaseModel):
    """Aggregate stats across all rows in the user's portfolio."""

    n: int
    cost_basis: float
    market_value: float
    open_pnl: float
    open_pnl_pct: float | None = None


class HoldingsResponse(BaseModel):
    holdings: list[HoldingOut]
    summary: HoldingsSummary
