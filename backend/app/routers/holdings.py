"""User-editable holdings endpoints.

Distinct from /positions/live which reflects the broker (Capital.com).
These are the user's manually-entered positions, persisted in Postgres.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.db import get_session
from app.modeling.data import latest_price
from app.models import Holding
from app.schemas import (
    HoldingIn,
    HoldingOut,
    HoldingsResponse,
    HoldingsSummary,
    HoldingUpdate,
)

router = APIRouter(prefix="/holdings", tags=["holdings"])


async def _enrich(session: AsyncSession, h: Holding) -> HoldingOut:
    last = await latest_price(session, h.ticker)
    last_price_value = last[1] if last else None

    qty = float(h.quantity)
    avg = float(h.avg_price)
    cost_basis = qty * avg
    market_value = qty * last_price_value if last_price_value is not None else None
    open_pnl = (
        market_value - cost_basis
        if market_value is not None
        else None
    )
    open_pnl_pct = (
        (open_pnl / cost_basis)
        if (open_pnl is not None and cost_basis > 0)
        else None
    )

    return HoldingOut(
        id=h.id,
        ticker=h.ticker,
        quantity=qty,
        avg_price=avg,
        notes=h.notes,
        added_at=h.added_at,
        updated_at=h.updated_at,
        last_price=last_price_value,
        market_value=market_value,
        cost_basis=cost_basis,
        open_pnl=open_pnl,
        open_pnl_pct=open_pnl_pct,
    )


@router.get("", response_model=HoldingsResponse)
async def list_holdings(
    session: AsyncSession = Depends(get_session),
) -> HoldingsResponse:
    """Every holding plus a portfolio-level summary."""
    rows = (
        await session.execute(select(Holding).order_by(Holding.ticker.asc()))
    ).scalars().all()

    enriched = [await _enrich(session, h) for h in rows]
    total_cost = sum(h.cost_basis or 0.0 for h in enriched)
    total_mv = sum(h.market_value or 0.0 for h in enriched if h.market_value is not None)
    total_pnl = total_mv - total_cost if total_mv > 0 else 0.0
    pnl_pct = (total_pnl / total_cost) if total_cost > 0 else None

    return HoldingsResponse(
        holdings=enriched,
        summary=HoldingsSummary(
            n=len(enriched),
            cost_basis=total_cost,
            market_value=total_mv,
            open_pnl=total_pnl,
            open_pnl_pct=pnl_pct,
        ),
    )


@router.post(
    "",
    response_model=HoldingOut,
    status_code=201,
    dependencies=[Depends(require_admin)],
)
async def create_holding(
    payload: HoldingIn,
    session: AsyncSession = Depends(get_session),
) -> HoldingOut:
    """Create a holding row. Errors if the ticker already exists — use PATCH."""
    existing = (
        await session.execute(
            select(Holding).where(Holding.ticker == payload.ticker.upper())
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Holding for {payload.ticker} already exists — use PATCH /holdings/{payload.ticker}",
        )

    h = Holding(
        ticker=payload.ticker.upper(),
        quantity=payload.quantity,
        avg_price=payload.avg_price,
        notes=payload.notes,
    )
    session.add(h)
    await session.commit()
    await session.refresh(h)
    return await _enrich(session, h)


@router.patch(
    "/{ticker}",
    response_model=HoldingOut,
    dependencies=[Depends(require_admin)],
)
async def update_holding(
    ticker: str,
    patch: HoldingUpdate,
    session: AsyncSession = Depends(get_session),
) -> HoldingOut:
    """Partial update of a holding."""
    h = (
        await session.execute(
            select(Holding).where(Holding.ticker == ticker.upper())
        )
    ).scalar_one_or_none()
    if h is None:
        raise HTTPException(404, f"No holding for {ticker}")

    if patch.quantity is not None:
        h.quantity = patch.quantity
    if patch.avg_price is not None:
        h.avg_price = patch.avg_price
    if patch.notes is not None:
        h.notes = patch.notes
    h.updated_at = datetime.now(UTC)

    await session.commit()
    await session.refresh(h)
    return await _enrich(session, h)


@router.delete(
    "/{ticker}",
    status_code=204,
    dependencies=[Depends(require_admin)],
)
async def delete_holding(
    ticker: str,
    session: AsyncSession = Depends(get_session),
) -> None:
    h = (
        await session.execute(
            select(Holding).where(Holding.ticker == ticker.upper())
        )
    ).scalar_one_or_none()
    if h is None:
        raise HTTPException(404, f"No holding for {ticker}")
    await session.delete(h)
    await session.commit()


@router.post(
    "/bulk",
    response_model=HoldingsResponse,
    dependencies=[Depends(require_admin)],
)
async def bulk_upsert_holdings(
    payload: list[HoldingIn],
    session: AsyncSession = Depends(get_session),
) -> HoldingsResponse:
    """Upsert a batch — convenient for pasting a whole portfolio in one shot."""
    for item in payload:
        existing = (
            await session.execute(
                select(Holding).where(Holding.ticker == item.ticker.upper())
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                Holding(
                    ticker=item.ticker.upper(),
                    quantity=item.quantity,
                    avg_price=item.avg_price,
                    notes=item.notes,
                )
            )
        else:
            existing.quantity = item.quantity
            existing.avg_price = item.avg_price
            existing.notes = item.notes
            existing.updated_at = datetime.now(UTC)
    await session.commit()
    return await list_holdings(session=session)
