"""User accounts — named investment portfolios with auto-classified risk profiles."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class UserAccount(Base):
    """A named portfolio belonging to one user or account context.

    Holdings are stored in AccountHolding rows. After each holdings change
    the engine classifies the account into the nearest of the 5 risk profiles
    and stores the result in ``assigned_profile_id``.
    """

    __tablename__ = "user_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    assigned_profile_id: Mapped[str | None] = mapped_column(String(64))
    profile_assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AccountHolding(Base):
    """One ticker position inside a user account."""

    __tablename__ = "account_holdings"
    __table_args__ = (
        CheckConstraint("quantity >= 0", name="account_holdings_quantity_nonneg"),
        CheckConstraint("avg_price >= 0", name="account_holdings_avg_price_nonneg"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    account_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    avg_price: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
