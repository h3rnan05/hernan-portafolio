"""Prediction — daily prediction per ticker, with realized backfill."""

import uuid
from datetime import date as DateType
from datetime import datetime

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class Prediction(Base):
    """Forward-looking prediction; `actual_price` is filled the next day.

    PK is (model_id, predicted_for) so we can re-predict the same date with a
    different model version without conflict.
    """

    __tablename__ = "predictions"

    model_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("models.id", ondelete="CASCADE"),
        primary_key=True,
    )
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    predicted_for: Mapped[DateType] = mapped_column(Date, primary_key=True, index=True)
    predicted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    predicted_return: Mapped[float | None] = mapped_column(Numeric(20, 8))
    predicted_price: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    actual_price: Mapped[float | None] = mapped_column(Numeric(20, 8))
    abs_error_pct: Mapped[float | None] = mapped_column(Numeric(10, 6))
