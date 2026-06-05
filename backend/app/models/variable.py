"""Variable registry — predictors, stocks, and portfolios."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    SmallInteger,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class Variable(Base):
    """A single time series we track.

    `kind` distinguishes:
        - 'predictor' : one of the 30 macro/market inputs
        - 'stock'     : one of the 9 portfolio stocks
        - 'portfolio' : the rolled-up portfolio value (computed, not ingested)

    `providers` is a JSON list of provider configs in priority order, e.g.:
        [
          {"name": "stooq",    "symbol": "^ftm"},
          {"name": "yfinance", "symbol": "^FTSE"}
        ]
    The ingestion runner walks this list and returns on first success.
    """

    __tablename__ = "variables"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('predictor', 'stock', 'etf', 'index', 'portfolio')",
            name="variables_kind_check",
        ),
        CheckConstraint(
            "transform IN ('return', 'level', 'surprise')",
            name="variables_transform_check",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str | None] = mapped_column(String(64))
    unit: Mapped[str | None] = mapped_column(String(32))
    providers: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_target: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # HER-15: per-variable lag override (NULL → global default) and how the raw
    # level becomes a model input ('return' | 'level' | 'surprise').
    lag_days: Mapped[int | None] = mapped_column(SmallInteger)
    transform: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="return"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
