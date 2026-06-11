"""Portfolio endpoints."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import ttl_cache
from app.db import get_session
from app.models import Observation, Portfolio, PortfolioSnapshot, Prediction
from app.schemas import (
    GrowthPoint,
    GrowthResponse,
    GrowthSeries,
    PortfolioOut,
    PortfolioSnapshotOut,
)

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
@ttl_cache(seconds=3600)
async def list_portfolios(
    response: Response,
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


# Benchmark preference order: VTI (NYSE_Composite id) is our broad-US-market
# proxy and the closest thing to the S&P 500 the registry tracks; QQQ second.
_BENCHMARK_CANDIDATES = [
    ("NYSE_Composite", "S&P 500 (VTI proxy)"),
    ("NASDAQ_Composite", "NASDAQ (QQQ proxy)"),
]

# Canonical display order; "P1_CONSERVATIVE" → short code "P1".
_PROFILE_ORDER = [
    "P1_CONSERVATIVE",
    "P2_MOD_CONSERVATIVE",
    "P3_BALANCED",
    "P4_MOD_AGGRESSIVE",
    "P5_AGGRESSIVE",
]


def _growth_from_prices(
    weights: dict[str, float],
    prices: dict[str, dict[date, float]],
    dates: list[date],
) -> list[GrowthPoint]:
    """Buy-and-hold growth of $10,000 from the start of the date axis.

    value(t) = 10000 · Σᵢ wᵢ · pᵢ(t) / pᵢ(t₀), with forward-fill across days a
    constituent didn't trade. Weights are renormalized over the tickers that
    actually have price data in the window.
    """
    # Forward-fill each ticker across the master axis; record its base price.
    ff: dict[str, list[float | None]] = {}
    base: dict[str, float] = {}
    for tkr in weights:
        series = prices.get(tkr, {})
        out: list[float | None] = []
        carry: float | None = None
        for d in dates:
            v = series.get(d)
            if v is not None:
                carry = v
            out.append(carry)
        ff[tkr] = out
        first = next((x for x in out if x is not None), None)
        if first:
            base[tkr] = first

    valid = {t: w for t, w in weights.items() if t in base and w > 0}
    wsum = sum(valid.values())
    if not valid or wsum <= 0:
        return []
    norm = {t: w / wsum for t, w in valid.items()}

    points: list[GrowthPoint] = []
    for i, d in enumerate(dates):
        total = 0.0
        ok = True
        for tkr, w in norm.items():
            p = ff[tkr][i]
            if p is None:  # before this ticker's first print in the window
                ok = False
                break
            total += w * (p / base[tkr])
        if ok:
            points.append(GrowthPoint(date=d.isoformat(), value=round(10000 * total, 2)))
    return points


@router.get("/growth", response_model=GrowthResponse)
@ttl_cache(seconds=3600)
async def portfolio_growth(
    response: Response,
    window: int = Query(90, description="Lookback window in days: 30, 90 or 360"),
    session: AsyncSession = Depends(get_session),
) -> GrowthResponse:
    """Growth of $10,000 per risk profile vs the market benchmark.

    Known v1 simplification: each profile's *current* weights are applied
    retroactively across the whole window (buy-and-hold from the window start,
    no rebalancing and no historical-weight drift). Good enough to compare the
    profiles' return shapes; revisit with PortfolioSnapshot weights for exact
    historical attribution.

    Reads only observations already in the DB — never fetches new data.
    """
    if window not in (30, 90, 360):
        raise HTTPException(422, "window must be one of 30, 90, 360")

    portfolios = (
        await session.execute(select(Portfolio).order_by(Portfolio.id.asc()))
    ).scalars().all()
    portfolios.sort(
        key=lambda p: _PROFILE_ORDER.index(p.id) if p.id in _PROFILE_ORDER else 99
    )

    # One query for every price the chart needs: constituents + benchmark.
    tickers: set[str] = set()
    for p in portfolios:
        tickers.update(k for k, w in (p.weights or {}).items() if w)
    bench_ids = [b[0] for b in _BENCHMARK_CANDIDATES]
    cutoff = date.today() - timedelta(days=window)
    rows = (
        await session.execute(
            select(Observation.variable_id, Observation.observed_on, Observation.value)
            .where(
                Observation.variable_id.in_(tickers | set(bench_ids)),
                Observation.observed_on >= cutoff,
            )
            .order_by(Observation.observed_on.asc())
        )
    ).all()

    prices: dict[str, dict[date, float]] = {}
    date_set: set[date] = set()
    for vid, d, val in rows:
        prices.setdefault(vid, {})[d] = float(val)
        if vid in tickers:  # benchmark-only dates shouldn't stretch the axis
            date_set.add(d)
    dates = sorted(date_set)

    series: list[GrowthSeries] = []
    for p in portfolios:
        weights = {k: float(v) for k, v in (p.weights or {}).items()}
        points = _growth_from_prices(weights, prices, dates)
        code = p.id.split("_")[0] if p.id in _PROFILE_ORDER else p.id
        label = p.name.removeprefix(f"{code} ").strip() or p.name
        series.append(GrowthSeries(profile=code, label=label, points=points))

    # Benchmark: first candidate with data in the window, as a 1-asset portfolio.
    for bench_id, bench_label in _BENCHMARK_CANDIDATES:
        if prices.get(bench_id):
            bench_dates = sorted(date_set | set(prices[bench_id].keys()))
            points = _growth_from_prices({bench_id: 1.0}, prices, bench_dates)
            if points:
                series.append(
                    GrowthSeries(profile="BENCH", label=bench_label, points=points)
                )
                break

    return GrowthResponse(window=window, series=series)


@router.get("/{portfolio_id}", response_model=PortfolioOut)
@ttl_cache(seconds=3600)
async def get_portfolio(
    portfolio_id: str,
    response: Response,
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


@router.get(
    "/{portfolio_id}/history",
    response_model=list[PortfolioSnapshotOut],
)
@ttl_cache(seconds=3600)
async def get_portfolio_history(
    portfolio_id: str,
    response: Response,
    days: int = 90,
    session: AsyncSession = Depends(get_session),
) -> list[PortfolioSnapshotOut]:
    """Append-only snapshots of weight evolution for one risk profile."""
    p = await session.get(Portfolio, portfolio_id)
    if p is None:
        raise HTTPException(404, f"Portfolio {portfolio_id} not found")

    cutoff = date.today() - timedelta(days=days)
    rows = (
        await session.execute(
            select(PortfolioSnapshot)
            .where(
                PortfolioSnapshot.portfolio_id == portfolio_id,
                PortfolioSnapshot.snapshotted_at >= cutoff,
            )
            .order_by(PortfolioSnapshot.snapshotted_at.asc())
        )
    ).scalars().all()

    return [
        PortfolioSnapshotOut(
            portfolio_id=s.portfolio_id,
            weights={k: float(v) for k, v in (s.weights or {}).items()},
            mape_30d=float(s.mape_30d) if s.mape_30d is not None else None,
            snapshotted_at=s.snapshotted_at,
        )
        for s in rows
    ]
