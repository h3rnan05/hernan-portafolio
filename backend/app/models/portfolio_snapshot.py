"""PortfolioSnapshot — append-only history of portfolio weight rebuilds."""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class PortfolioSnapshot(Base):
    """One frozen-in-time snapshot of a portfolio profile's weights + MAPE.

    Separate from `Portfolio` (which is the current/latest UPSERT). Every
    cron rebuild appends one row here so we can chart how the algorithm's
    asset allocation drifts over time.
    """

    __tablename__ = "portfolio_snapshots"
    __table_args__ = (
        Index("ix_portfolio_snapshots_lookup", "portfolio_id", "snapshotted_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    portfolio_id: Mapped[str] = mapped_column(String(64), nullable=False)
    weights: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False)
    mape_30d: Mapped[float | None] = mapped_column(Numeric(10, 6))
    snapshotted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
