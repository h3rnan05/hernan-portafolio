"""Prediction endpoints: per-ticker history, portfolio rollup, simulator."""

from __future__ import annotations

import math
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
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


@router.get("/accuracy")
@ttl_cache(seconds=60)
async def accuracy_summary(
    response: Response,
    days: int = Query(90, ge=7, le=365),
    min_signal: float = Query(0.0, ge=0.0, le=0.05, description="Min |predicted_return| to count in hit-rate (0 = no filter)"),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Hit rate + MAPE per ticker for the last N days.

    ``min_signal`` is a confidence threshold: predictions where
    ``|predicted_return| < min_signal`` are excluded from the directional
    hit-rate calculation. This lets callers see how accuracy changes when
    the bot only trades high-conviction days.
    """
    cutoff = date.today() - timedelta(days=days)
    # Deduplicate: when multiple model versions made predictions for the same
    # (ticker, date), keep only the row from the highest model_id (latest refit).
    # This avoids double-counting without discarding historical predictions.
    latest_model = (
        select(
            Prediction.ticker,
            Prediction.predicted_for,
            func.max(Prediction.model_id).label("max_model_id"),
        )
        .where(Prediction.predicted_for >= cutoff)
        .group_by(Prediction.ticker, Prediction.predicted_for)
        .subquery()
    )
    stmt = (
        select(Prediction)
        .join(
            latest_model,
            (Prediction.ticker == latest_model.c.ticker)
            & (Prediction.predicted_for == latest_model.c.predicted_for)
            & (Prediction.model_id == latest_model.c.max_model_id),
        )
        .order_by(Prediction.ticker.asc(), Prediction.predicted_for.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()

    by_ticker: dict[str, list[Prediction]] = {}
    for p in rows:
        by_ticker.setdefault(p.ticker, []).append(p)

    results = []
    for ticker, preds in sorted(by_ticker.items()):
        with_actual = [p for p in preds if p.actual_price is not None]
        n_total = len(preds)
        n_actual = len(with_actual)

        mape = None
        if n_actual:
            mape = sum(float(p.abs_error_pct) for p in with_actual if p.abs_error_pct) / n_actual

        # Directional hit rate with optional confidence filter
        hit, total_dir, filtered_out = 0, 0, 0
        for i in range(1, len(with_actual)):
            prev_price = float(with_actual[i - 1].actual_price)
            curr_price = float(with_actual[i].actual_price)
            pred_ret   = float(with_actual[i].predicted_return) if with_actual[i].predicted_return else 0
            actual_dir = curr_price - prev_price
            if actual_dir == 0 or pred_ret == 0:
                continue
            # Skip low-conviction days when threshold is active
            if min_signal > 0 and abs(pred_ret) < min_signal:
                filtered_out += 1
                continue
            if (actual_dir > 0) == (pred_ret > 0):
                hit += 1
            total_dir += 1

        hit_rate = hit / total_dir if total_dir > 0 else None

        results.append({
            "ticker":        ticker,
            "n_total":       n_total,
            "n_actual":      n_actual,
            "n_filtered":    filtered_out,
            "hit_rate":      round(hit_rate, 4) if hit_rate is not None else None,
            "mape":          round(mape, 6) if mape is not None else None,
            "days":          days,
            "min_signal":    min_signal,
        })

    return results


def _mape(points: list[PredictionPoint]) -> float | None:
    pairs = [
        (p.predicted_price, p.actual_price)
        for p in points
        if p.actual_price is not None and p.actual_price != 0
    ]
    if not pairs:
        return None
    return sum(abs(pred - act) / abs(act) for pred, act in pairs) / len(pairs)


def _directional_accuracy(points: list[PredictionPoint]) -> float | None:
    """% of days where predicted direction (sign of predicted_return) matches actual direction."""
    filled = [p for p in points if p.actual_price is not None and p.predicted_return is not None]
    if len(filled) < 2:
        return None
    correct = 0
    total = 0
    for i in range(1, len(filled)):
        actual_dir = filled[i].actual_price - filled[i - 1].actual_price  # type: ignore[operator]
        pred_dir = filled[i].predicted_return
        if actual_dir == 0 or pred_dir == 0:
            continue
        if (actual_dir > 0) == (pred_dir > 0):
            correct += 1
        total += 1
    return correct / total if total > 0 else None


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
    latest_model_t = (
        select(
            Prediction.predicted_for,
            func.max(Prediction.model_id).label("max_model_id"),
        )
        .where(Prediction.ticker == ticker, Prediction.predicted_for >= cutoff)
        .group_by(Prediction.predicted_for)
        .subquery()
    )
    stmt = (
        select(Prediction)
        .join(
            latest_model_t,
            (Prediction.predicted_for == latest_model_t.c.predicted_for)
            & (Prediction.model_id == latest_model_t.c.max_model_id),
        )
        .where(Prediction.ticker == ticker)
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
    return TickerPredictions(ticker=ticker, points=points, mape=_mape(points), directional_accuracy=_directional_accuracy(points))


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
