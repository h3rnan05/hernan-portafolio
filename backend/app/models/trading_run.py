from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class TradingRun(Base):
    __tablename__ = "trading_runs"

    id:      Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ran_at:  Mapped[datetime]  = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    bot:     Mapped[str]       = mapped_column(String(32))
    status:  Mapped[str]       = mapped_column(String(16), default="ok")
    trades:  Mapped[int]       = mapped_column(Integer, default=0)
    error:   Mapped[str | None] = mapped_column(Text, nullable=True)
