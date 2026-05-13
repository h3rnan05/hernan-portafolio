"""PositionSnapshot — Capital.com positions captured at a point in time."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class PositionSnapshot(Base):
    """A point-in-time capture of one position from Capital.com."""

    __tablename__ = "positions_snapshots"
    __table_args__ = (
        Index("ix_positions_snap_lookup", "snapshot_at", "ticker"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    avg_price: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    last_price: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    market_value: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    open_pnl: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    open_pnl_pct: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
