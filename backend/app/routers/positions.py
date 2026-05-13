"""Live positions endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.db import AsyncSessionLocal, get_session
from app.ingestion.capital_com import CapitalComError
from app.ingestion.positions_snapshot import snapshot_positions
from app.models import PositionSnapshot
from app.schemas import PositionOut, PositionsSyncOut

router = APIRouter(prefix="/positions", tags=["positions"])


@router.get("/live", response_model=list[PositionOut])
async def live_positions(
    session: AsyncSession = Depends(get_session),
) -> list[PositionOut]:
    """Most-recent snapshot, deduped per ticker."""
    # Window-function approach: pick the latest row per ticker
    sub = (
        select(
            PositionSnapshot.id,
            PositionSnapshot.snapshot_at,
            PositionSnapshot.account_id,
            PositionSnapshot.ticker,
            PositionSnapshot.quantity,
            PositionSnapshot.avg_price,
            PositionSnapshot.last_price,
            PositionSnapshot.market_value,
            PositionSnapshot.open_pnl,
            PositionSnapshot.open_pnl_pct,
        )
        .order_by(PositionSnapshot.ticker.asc(), desc(PositionSnapshot.snapshot_at))
    )
    rows = (await session.execute(sub)).all()
    seen: set[str] = set()
    out: list[PositionOut] = []
    for r in rows:
        if r.ticker in seen:
            continue
        seen.add(r.ticker)
        out.append(
            PositionOut(
                snapshot_at=r.snapshot_at,
                account_id=r.account_id,
                ticker=r.ticker,
                quantity=float(r.quantity),
                avg_price=float(r.avg_price),
                last_price=float(r.last_price),
                market_value=float(r.market_value),
                open_pnl=float(r.open_pnl),
                open_pnl_pct=float(r.open_pnl_pct),
            )
        )
    return out


@router.post(
    "/sync",
    response_model=PositionsSyncOut,
    dependencies=[Depends(require_admin)],
)
async def sync_positions() -> PositionsSyncOut:
    """Force-pull current positions from Capital.com into a new snapshot."""
    async with AsyncSessionLocal() as session:
        try:
            n = await snapshot_positions(session)
        except CapitalComError as e:
            raise HTTPException(502, f"Capital.com error: {e}") from e
    return PositionsSyncOut(snapshot_count=n, snapshot_at=datetime.now(UTC))
