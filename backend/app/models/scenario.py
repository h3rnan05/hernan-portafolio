"""Scenario — a named macro/geopolitical situation grouping the 5 risk profiles.

Each scenario owns one set of P1–P5 portfolio rows (see Portfolio.scenario_id).
`build_mode='algorithmic'` scenarios are rebuilt daily by the prediction job;
`'static'` scenarios carry curated, fixed weights. Exactly one scenario is
`is_default=True` (surfaced on the /portfolios page).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class Scenario(Base):
    __tablename__ = "scenarios"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # geopolitical | environmental | political | peacetime | …
    scenario_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="geopolitical"
    )
    # public | draft
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    # algorithmic (daily-rebuilt) | static (curated fixed weights)
    build_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="algorithmic"
    )
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
