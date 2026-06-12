"""Portfolio — the 5 risk profiles with their weights."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class Portfolio(Base):
    """A portfolio is a named bag of weights summing to 1.0.

    The 5 standard profiles seeded in v1 are:
        P1_CONSERVATIVE, P2_MOD_CONSERVATIVE, P3_BALANCED,
        P4_MOD_AGGRESSIVE, P5_AGGRESSIVE
    """

    __tablename__ = "portfolios"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    weights: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
