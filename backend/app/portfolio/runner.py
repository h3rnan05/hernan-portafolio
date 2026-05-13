"""Daily prediction runner + actual-price backfiller + portfolio recompute.

Hooked into cron via ``scripts/run_predictions.py``. The module-level
functions are async so they can be called from FastAPI handlers too (the
admin /predictions/run endpoint, etc.).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
import structlog
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modeling.data import latest_price, load_returns_frame
from app.modeling.prediction import prediction_record
from app.models import ModelFit, Observation, Portfolio, Prediction
from app.portfolio.optimizer import PROFILE_DESCRIPTIONS, build_portfolios

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class PredictionOutcome:
    ticker: str
    predicted_for: date
    predicted_return: float
    predicted_price: float
    last_price: float


async def run_daily_predictions(
    session: AsyncSession,
    *,
    target_date: date | None = None,
    lag_days: int = 1,
) -> list[PredictionOutcome]:
    """For each active model, compute today's prediction from yesterday's data.

    Idempotent — uses ON CONFLICT (model_id, predicted_for) DO UPDATE.
    """
    target_date = target_date or date.today()
    out: list[PredictionOutcome] = []

    active_models = (
        await session.execute(
            select(ModelFit).where(ModelFit.is_active.is_(True))
        )
    ).scalars().all()

    if not active_models:
        log.warning("predictions_no_active_models")
        return out

    # Load enough history to grab lag_days+1 days of returns for all predictors
    all_predictors = sorted({p for m in active_models for p in m.predictor_ids})
    end = target_date
    start = end - timedelta(days=30)  # generous buffer for weekend/holiday
    returns = await load_returns_frame(
        session,
        variable_ids=all_predictors,
        start=start,
        end=end,
    )

    if returns.empty:
        log.warning("predictions_no_returns", start=start, end=end)
        return out

    # Pick the most-recent row whose timestamp is on or before target_date
    cutoff = pd.Timestamp(target_date)
    eligible = returns.index[returns.index <= cutoff]
    if len(eligible) < lag_days:
        log.warning("predictions_insufficient_history", n=len(eligible))
        return out
    lagged_row = returns.loc[eligible[-1]]  # this is the lag_days=1 input

    rows_to_upsert = []
    for m in active_models:
        last = await latest_price(session, m.ticker)
        if last is None:
            log.warning("predictions_no_last_price", ticker=m.ticker)
            continue
        _last_date, last_price_value = last

        # Pull lagged returns for this model's predictors
        lagged: dict[str, float] = {}
        skip = False
        for p in m.predictor_ids:
            if p not in lagged_row or pd.isna(lagged_row[p]):
                log.warning(
                    "predictions_missing_predictor",
                    ticker=m.ticker,
                    predictor=p,
                )
                skip = True
                break
            lagged[p] = float(lagged_row[p])
        if skip:
            continue

        rec = prediction_record(
            ticker=m.ticker,
            predicted_for=target_date,
            last_price=last_price_value,
            intercept=float(m.intercept),
            coefficients={k: float(v) for k, v in m.coefficients.items()},
            lagged_returns=lagged,
        )

        rows_to_upsert.append(
            {
                "model_id": m.id,
                "ticker": m.ticker,
                "predicted_for": rec["predicted_for"],
                "predicted_return": rec["predicted_return"],
                "predicted_price": rec["predicted_price"],
            }
        )
        out.append(
            PredictionOutcome(
                ticker=m.ticker,
                predicted_for=rec["predicted_for"],
                predicted_return=rec["predicted_return"],
                predicted_price=rec["predicted_price"],
                last_price=last_price_value,
            )
        )

    if rows_to_upsert:
        stmt = pg_insert(Prediction).values(rows_to_upsert)
        stmt = stmt.on_conflict_do_update(
            index_elements=["model_id", "predicted_for"],
            set_={
                "ticker": stmt.excluded.ticker,
                "predicted_return": stmt.excluded.predicted_return,
                "predicted_price": stmt.excluded.predicted_price,
            },
        )
        await session.execute(stmt)

    await session.commit()
    log.info("predictions_done", n=len(out), date=target_date.isoformat())
    return out


async def backfill_actuals(
    session: AsyncSession,
    *,
    target_date: date | None = None,
) -> int:
    """Fill ``actual_price`` + ``abs_error_pct`` on yesterday's predictions.

    Returns the number of rows updated.
    """
    target_date = target_date or (date.today() - timedelta(days=1))

    # Predictions awaiting actuals on or before target_date
    pending = (
        await session.execute(
            select(Prediction).where(
                Prediction.actual_price.is_(None),
                Prediction.predicted_for <= target_date,
            )
        )
    ).scalars().all()

    if not pending:
        return 0

    n_updated = 0
    for p in pending:
        obs = (
            await session.execute(
                select(Observation.value)
                .where(
                    Observation.variable_id == p.ticker,
                    Observation.observed_on == p.predicted_for,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if obs is None:
            continue

        actual = float(obs)
        pred = float(p.predicted_price)
        err = abs(pred - actual) / actual if actual != 0 else None

        await session.execute(
            update(Prediction)
            .where(
                Prediction.model_id == p.model_id,
                Prediction.predicted_for == p.predicted_for,
            )
            .values(actual_price=actual, abs_error_pct=err)
        )
        n_updated += 1

    await session.commit()
    log.info("backfill_actuals_done", n_updated=n_updated)
    return n_updated


async def rebuild_portfolios(
    session: AsyncSession,
    *,
    days_for_metrics: int = 90,
) -> dict[str, dict[str, float]]:
    """Recompute the 5 weight profiles and upsert into the ``portfolios`` table."""
    # Tickers + active models with their R²
    active_models = (
        await session.execute(
            select(ModelFit).where(ModelFit.is_active.is_(True))
        )
    ).scalars().all()
    if not active_models:
        log.warning("portfolios_no_active_models")
        return {}

    tickers = [m.ticker for m in active_models]
    end = date.today()
    start = end - timedelta(days=days_for_metrics + 10)

    returns = await load_returns_frame(
        session,
        variable_ids=tickers,
        start=start,
        end=end,
    )
    if returns.empty:
        log.warning("portfolios_no_returns")
        return {}

    metrics_rows = []
    for m in active_models:
        s = returns[m.ticker].dropna() if m.ticker in returns.columns else pd.Series(dtype=float)
        if len(s) < 30:
            metrics_rows.append({
                "ticker": m.ticker,
                "r2": float(m.r2),
                "pred_ret_30d": 0.0,
                "vol_annual": 0.0,
                "sharpe": 0.0,
            })
            continue
        vol_annual = float(s.std() * math.sqrt(252))
        # Use mean daily return × 30 as a coarse 30-day forecast (true forecast
        # comes from the predictions table — this method is the §5 fallback when
        # the predictions table hasn't been populated yet).
        pred_ret_30d = float(s.mean() * 30.0)
        sharpe = (
            pred_ret_30d * (252.0 / 30.0) / vol_annual
            if vol_annual > 0
            else 0.0
        )
        metrics_rows.append({
            "ticker": m.ticker,
            "r2": float(m.r2),
            "pred_ret_30d": pred_ret_30d,
            "vol_annual": vol_annual,
            "sharpe": sharpe,
        })

    metrics_df = pd.DataFrame(metrics_rows).set_index("ticker")
    profiles = build_portfolios(metrics_df)

    rows = [
        {
            "id": pid,
            "name": pid.replace("_", " ").title(),
            "description": PROFILE_DESCRIPTIONS.get(pid, ""),
            "weights": weights,
        }
        for pid, weights in profiles.items()
    ]

    stmt = pg_insert(Portfolio).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["id"],
        set_={
            "name": stmt.excluded.name,
            "description": stmt.excluded.description,
            "weights": stmt.excluded.weights,
        },
    )
    await session.execute(stmt)
    await session.commit()
    log.info("portfolios_rebuilt", n_profiles=len(profiles))
    return profiles
