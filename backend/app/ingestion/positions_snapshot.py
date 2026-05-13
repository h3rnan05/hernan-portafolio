"""Positions snapshot job — pulls Capital.com positions and writes one row each."""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.capital_com import CapitalComClient, CapitalComError
from app.models import PositionSnapshot

log = structlog.get_logger(__name__)


async def snapshot_positions(
    session: AsyncSession,
    *,
    client: CapitalComClient | None = None,
) -> int:
    """Fetch live positions from Capital.com and persist a row per ticker.

    Returns the number of rows inserted. Failures (auth, network) bubble up
    so the cron runner can mark the step as failed without partial inserts.
    """
    client = client or CapitalComClient()
    try:
        positions = await client.get_positions()
    except CapitalComError as e:
        log.error("capital_snapshot_failed", err=str(e))
        raise

    rows = [
        PositionSnapshot(
            account_id=p.account_id,
            ticker=p.ticker,
            quantity=p.quantity,
            avg_price=p.avg_price,
            last_price=p.last_price,
            market_value=p.market_value,
            open_pnl=p.open_pnl,
            open_pnl_pct=p.open_pnl_pct,
        )
        for p in positions
    ]

    session.add_all(rows)
    await session.commit()
    log.info("capital_snapshot_done", n=len(rows))
    return len(rows)
