"""Portfolio endpoints."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Portfolio, Prediction
from app.schemas import PortfolioOut

router = APIRouter(prefix="/portfolios", tags=["portfolios"])


async def _portfolio_mape_30d(
    session: AsyncSession, portfolio_id: str, weights: dict[str, float]
) -> float | None:
    """Compute the 30-day rolled-up MAPE for a single portfolio.

    Returns None when there's no backfilled actual data yet.
    """
    cutoff = date.today() - timedelta(days=30)
    rows = (
        await session.execute(
            select(
                Prediction.predicted_for,
                Prediction.ticker,
                Prediction.predicted_price,
                Prediction.actual_price,
            )
            .where(
                Prediction.predicted_for >= cutoff,
                Prediction.actual_price.is_not(None),
            )
        )
    ).all()
    if not rows:
        return None

    by_date: dict[date, dict[str, tuple[float, float]]] = {}
    for d, t, pred, act in rows:
        by_date.setdefault(d, {})[t] = (float(pred), float(act))

    pairs: list[tuple[float, float]] = []
    for per_ticker in by_date.values():
        valid = {tkr: w for tkr, w in weights.items() if tkr in per_ticker}
        if not valid:
            continue
        wsum = sum(valid.values())
        if wsum <= 0:
            continue
        norm = {k: v / wsum for k, v in valid.items()}
        pv = sum(norm[tkr] * per_ticker[tkr][0] for tkr in norm)
        av = sum(norm[tkr] * per_ticker[tkr][1] for tkr in norm)
        if av != 0:
            pairs.append((pv, av))

    if not pairs:
        return None
    return sum(abs(p - a) / abs(a) for p, a in pairs) / len(pairs)


@router.get("", response_model=list[PortfolioOut])
async def list_portfolios(
    session: AsyncSession = Depends(get_session),
) -> list[PortfolioOut]:
    """List the 5 risk profiles, with 30-day rolled-up MAPE when available."""
    rows = (
        await session.execute(select(Portfolio).order_by(Portfolio.id.asc()))
    ).scalars().all()
    out: list[PortfolioOut] = []
    for p in rows:
        weights = {k: float(v) for k, v in (p.weights or {}).items()}
        mape = await _portfolio_mape_30d(session, p.id, weights)
        out.append(
            PortfolioOut(
                id=p.id,
                name=p.name,
                description=p.description,
                weights=weights,
                generated_at=p.generated_at,
                mape_30d=mape,
            )
        )
    return out


@router.get("/{portfolio_id}", response_model=PortfolioOut)
async def get_portfolio(
    portfolio_id: str,
    session: AsyncSession = Depends(get_session),
) -> PortfolioOut:
    p = await session.get(Portfolio, portfolio_id)
    if p is None:
        raise HTTPException(404, f"Portfolio {portfolio_id} not found")
    weights = {k: float(v) for k, v in (p.weights or {}).items()}
    mape = await _portfolio_mape_30d(session, p.id, weights)
    return PortfolioOut(
        id=p.id,
        name=p.name,
        description=p.description,
        weights=weights,
        generated_at=p.generated_at,
        mape_30d=mape,
    )
