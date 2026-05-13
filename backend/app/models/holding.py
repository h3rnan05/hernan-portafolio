"""Holding — one row of the user's editable portfolio."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class Holding(Base):
    """User-editable holding (qty + average cost per ticker).

    Separate from ``positions_snapshots`` (which is Capital.com-sourced and
    append-only). This is the user's manually-curated record.
    """

    __tablename__ = "holdings"
    __table_args__ = (
        CheckConstraint("quantity >= 0", name="holdings_quantity_nonneg"),
        CheckConstraint("avg_price >= 0", name="holdings_avg_price_nonneg"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    quantity: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    avg_price: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
