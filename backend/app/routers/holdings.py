"""User-editable holdings endpoints.

Distinct from /positions/live which reflects a broker mirror. These are
the user's manually-entered positions, persisted in Postgres.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.db import get_session
from app.modeling.data import latest_price
from app.models import Holding, ModelFit, Prediction
from app.schemas import (
    HoldingIn,
    HoldingOut,
    HoldingProjection,
    HoldingsProjectionOut,
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


@router.get("/projection", response_model=HoldingsProjectionOut)
async def holdings_projection(
    session: AsyncSession = Depends(get_session),
) -> HoldingsProjectionOut:
    """Compare each holding's current market value against the latest model
    prediction for that ticker. Roll up into a portfolio-level delta so the
    user sees: "if today's predictions play out, where am I tomorrow?"
    """
    holdings = (
        await session.execute(select(Holding).order_by(Holding.ticker.asc()))
    ).scalars().all()

    # Latest prediction per ticker (active model only)
    active_models = (
        await session.execute(select(ModelFit).where(ModelFit.is_active.is_(True)))
    ).scalars().all()
    active_tickers = {m.ticker: m.id for m in active_models}

    pred_by_ticker: dict[str, float] = {}
    for ticker, model_id in active_tickers.items():
        row = (
            await session.execute(
                select(Prediction.predicted_price)
                .where(
                    Prediction.ticker == ticker,
                    Prediction.model_id == model_id,
                )
                .order_by(desc(Prediction.predicted_for))
                .limit(1)
            )
        ).first()
        if row is not None:
            pred_by_ticker[ticker] = float(row[0])

    rows: list[HoldingProjection] = []
    current_mv = 0.0
    projected_mv = 0.0
    for h in holdings:
        last = await latest_price(session, h.ticker)
        last_price_value = last[1] if last else 0.0
        qty = float(h.quantity)
        avg = float(h.avg_price)
        mv = qty * last_price_value
        open_pnl = mv - qty * avg
        open_pnl_pct = (open_pnl / (qty * avg)) if (qty * avg) > 0 else 0.0

        pred_price = pred_by_ticker.get(h.ticker)
        pred_mv = qty * pred_price if pred_price is not None else None
        pred_pnl_delta = (pred_mv - mv) if pred_mv is not None else None

        rows.append(
            HoldingProjection(
                ticker=h.ticker,
                quantity=qty,
                avg_price=avg,
                last_price=last_price_value,
                market_value=mv,
                open_pnl=open_pnl,
                open_pnl_pct=open_pnl_pct,
                predicted_price=pred_price,
                predicted_market_value=pred_mv,
                predicted_pnl_delta=pred_pnl_delta,
            )
        )
        current_mv += mv
        projected_mv += pred_mv if pred_mv is not None else mv

    delta = projected_mv - current_mv
    delta_pct = (delta / current_mv) if current_mv > 0 else None

    return HoldingsProjectionOut(
        rows=rows,
        current_market_value=current_mv,
        projected_market_value=projected_mv,
        projected_delta=delta,
        projected_delta_pct=delta_pct,
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
