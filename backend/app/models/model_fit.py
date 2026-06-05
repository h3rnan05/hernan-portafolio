"""ModelFit — fitted regression model per stock per refit cycle."""

import uuid
from datetime import date as DateType
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class ModelFit(Base):
    """One fit of a regression model for one stock.

    Multiple ModelFit rows per ticker may exist (history of refits); only one
    has `is_active=True`. The partial unique index enforces that.
    """

    __tablename__ = "models"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PASS', 'REVIEW', 'FAIL')",
            name="models_status_check",
        ),
        Index(
            "uniq_active_model",
            "ticker",
            unique=True,
            postgresql_where="is_active = true",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    fitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    training_start: Mapped[DateType] = mapped_column(Date, nullable=False)
    training_end: Mapped[DateType] = mapped_column(Date, nullable=False)
    n_obs: Mapped[int] = mapped_column(Integer, nullable=False)
    predictor_ids: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    intercept: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    coefficients: Mapped[dict] = mapped_column(JSON, nullable=False)
    r2: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    r2_adj: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    durbin_watson: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    breusch_pagan_p: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    max_vif: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    # Residual standard error of the fit — the 1-day return sigma used to build
    # the HER-17 forecast confidence band. Nullable for models fit before HER-16.
    resid_std: Mapped[float | None] = mapped_column(Numeric(20, 8))
    # Estimator + regularization strength (HER-16). Legacy rows are 'ols'.
    estimator: Mapped[str] = mapped_column(String(8), nullable=False, server_default="ols")
    alpha: Mapped[float | None] = mapped_column(Numeric(20, 8))
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
