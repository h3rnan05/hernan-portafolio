"""Prediction endpoints: per-ticker history, portfolio rollup, simulator."""

from __future__ import annotations

import math
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import ttl_cache
from app.db import get_session
from app.modeling.data import latest_price
from app.modeling.prediction import predict_next_return
from app.models import ModelFit, Portfolio, Prediction
from app.schemas import (
    PortfolioPredictionPoint,
    PortfolioPredictions,
    PredictionPoint,
    SimulatedTicker,
    SimulateRequest,
    SimulateResponse,
    TickerPredictions,
)

router = APIRouter(prefix="/predictions", tags=["predictions"])


def _mape(points: list[PredictionPoint]) -> float | None:
    pairs = [
        (p.predicted_price, p.actual_price)
        for p in points
        if p.actual_price is not None and p.actual_price != 0
    ]
    if not pairs:
        return None
    return sum(abs(pred - act) / abs(act) for pred, act in pairs) / len(pairs)


@router.get("/portfolio/{portfolio_id}", response_model=PortfolioPredictions)
@ttl_cache(seconds=3600)
async def portfolio_predictions(
    portfolio_id: str,
    response: Response,
    days: int = Query(30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
) -> PortfolioPredictions:
    """Roll up per-ticker predictions into a portfolio-level series."""
    portfolio = await session.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise HTTPException(404, f"Portfolio {portfolio_id} not found")

    cutoff = date.today() - timedelta(days=days)
    rows = (
        await session.execute(
            select(
                Prediction.predicted_for,
                Prediction.ticker,
                Prediction.predicted_price,
                Prediction.actual_price,
            )
            .where(Prediction.predicted_for >= cutoff)
            .order_by(Prediction.predicted_for.asc())
        )
    ).all()

    # Group rows by date → {ticker: (pred, actual)}
    by_date: dict[date, dict[str, tuple[float, float | None]]] = {}
    for row in rows:
        d, t, pred, actual = row
        by_date.setdefault(d, {})[t] = (
            float(pred),
            float(actual) if actual is not None else None,
        )

    weights = portfolio.weights or {}
    points: list[PortfolioPredictionPoint] = []
    for d in sorted(by_date.keys()):
        per_ticker = by_date[d]
        # Compute weighted predicted_value over tickers actually in this row
        valid_w = {tkr: w for tkr, w in weights.items() if tkr in per_ticker}
        if not valid_w:
            continue
        wsum = sum(valid_w.values())
        norm = {k: v / wsum for k, v in valid_w.items()} if wsum > 0 else valid_w
        pv = sum(norm[tkr] * per_ticker[tkr][0] for tkr in norm)
        if all(per_ticker[tkr][1] is not None for tkr in norm):
            av = sum(norm[tkr] * per_ticker[tkr][1] for tkr in norm)  # type: ignore[operator]
            err = abs(pv - av) / av if av else None
        else:
            av = None
            err = None
        points.append(
            PortfolioPredictionPoint(
                predicted_for=d, predicted_value=pv, actual_value=av, error_pct=err
            )
        )

    actual_pairs = [(p.predicted_value, p.actual_value) for p in points if p.actual_value]
    mape = (
        sum(abs(pp - aa) / aa for pp, aa in actual_pairs) / len(actual_pairs)
        if actual_pairs
        else None
    )
    return PortfolioPredictions(portfolio_id=portfolio_id, points=points, mape=mape)


@router.get("/{ticker}", response_model=TickerPredictions)
@ttl_cache(seconds=3600)
async def get_predictions(
    ticker: str,
    response: Response,
    days: int = Query(30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
) -> TickerPredictions:
    """Last N predictions for a ticker, with backfilled actuals where present."""
    cutoff = date.today() - timedelta(days=days)
    stmt = (
        select(Prediction)
        .where(Prediction.ticker == ticker, Prediction.predicted_for >= cutoff)
        .order_by(Prediction.predicted_for.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    points = [
        PredictionPoint(
            predicted_for=p.predicted_for,
            predicted_at=p.predicted_at,
            predicted_return=float(p.predicted_return) if p.predicted_return is not None else None,
            predicted_price=float(p.predicted_price),
            actual_price=float(p.actual_price) if p.actual_price is not None else None,
            abs_error_pct=float(p.abs_error_pct) if p.abs_error_pct is not None else None,
        )
        for p in rows
    ]
    return TickerPredictions(ticker=ticker, points=points, mape=_mape(points))


@router.post("/simulate", response_model=SimulateResponse)
async def simulate(
    body: SimulateRequest,
    session: AsyncSession = Depends(get_session),
) -> SimulateResponse:
    """Apply hypothetical lagged-returns to every active model and roll up.

    Predictors not supplied default to 0.0, so the response delta represents
    the marginal effect of the supplied inputs on top of "zero shock".
    """
    rows = (
        await session.execute(select(ModelFit).where(ModelFit.is_active.is_(True)))
    ).scalars().all()
    if not rows:
        raise HTTPException(404, "No active models to simulate against")

    # Resolve last prices once per ticker
    per_ticker: list[SimulatedTicker] = []
    portfolio_value = 0.0
    portfolio_value_baseline = 0.0

    for m in rows:
        last = await latest_price(session, m.ticker)
        if last is None:
            continue
        _, last_price_value = last

        coefs = {k: float(v) for k, v in (m.coefficients or {}).items()}
        # Build lagged-returns dict: supplied where present, else 0.0
        lagged = {p: float(body.inputs.get(p, 0.0)) for p in coefs}
        contributions = {p: float(coefs[p] * lagged[p]) for p in coefs}

        ret = predict_next_return(float(m.intercept), coefs, lagged)
        price = last_price_value * math.exp(ret)
        per_ticker.append(
            SimulatedTicker(
                ticker=m.ticker,
                predicted_return=ret,
                predicted_price=price,
                last_price=last_price_value,
                contributions=contributions,
            )
        )
        portfolio_value += price
        portfolio_value_baseline += last_price_value

    return SimulateResponse(
        inputs=body.inputs,
        horizon_days=body.horizon_days,
        per_ticker=per_ticker,
        portfolio_value=portfolio_value,
        portfolio_value_baseline=portfolio_value_baseline,
        delta=portfolio_value - portfolio_value_baseline,
    )
