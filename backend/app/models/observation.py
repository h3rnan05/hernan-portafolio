"""Observation — one (variable, date, value) tuple."""

import uuid
from datetime import date as DateType

from sqlalchemy import Date, ForeignKey, Index, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Observation(Base):
    """A single value of a single variable on a single date.

    Composite PK on (variable_id, observed_on) makes the ingestion idempotent —
    re-running a day uses ON CONFLICT (variable_id, observed_on) DO UPDATE.
    """

    __tablename__ = "observations"
    __table_args__ = (
        Index(
            "ix_obs_var_date_desc",
            "variable_id",
            "observed_on",
            postgresql_using="btree",
        ),
    )

    variable_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("variables.id", ondelete="CASCADE"),
        primary_key=True,
    )
    observed_on: Mapped[DateType] = mapped_column(Date, primary_key=True)
    value: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    source_run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("ingestion_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    served_by_provider: Mapped[str | None] = mapped_column(String(32))
