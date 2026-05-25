"""Schemas for user accounts and their risk-profile classification."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class AccountHoldingIn(BaseModel):
    ticker: str
    quantity: float
    avg_price: float
    notes: str | None = None


class AccountHoldingOut(BaseModel):
    id: uuid.UUID
    ticker: str
    quantity: float
    avg_price: float
    notes: str | None = None
    added_at: datetime
    last_price: float | None = None
    market_value: float | None = None
    open_pnl: float | None = None
    open_pnl_pct: float | None = None


class ProfileMatch(BaseModel):
    """Which of the 5 risk profiles best matches the account's current weights."""

    profile_id: str
    profile_name: str
    distance: float  # L2 distance; lower = closer match


class UserAccountCreate(BaseModel):
    name: str
    description: str | None = None
    holdings: list[AccountHoldingIn] = []


class UserAccountPatch(BaseModel):
    name: str | None = None
    description: str | None = None


class UserAccountOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    assigned_profile_id: str | None = None
    profile_assigned_at: datetime | None = None
    created_at: datetime
    holdings: list[AccountHoldingOut] = []
    profile_match: ProfileMatch | None = None
    total_market_value: float | None = None
