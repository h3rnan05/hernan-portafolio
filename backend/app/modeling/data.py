"""DB I/O for the modeling layer.

Pure functions stay in regression/feature_select/prediction so they're trivial
to test. Anything that touches SQLAlchemy lives here.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ModelFit, Observation, Variable


async def load_returns_frame(
    session: AsyncSession,
    *,
    variable_ids: list[str],
    start: date | None = None,
    end: date | None = None,
) -> pd.DataFrame:
    """Build a wide DataFrame of **log returns** for the requested variables.

    Mixed-frequency-safe: each series is forward-filled to the daily
    business-day calendar so monthly macro releases (CPI, unemployment, …)
    carry forward until the next release. Log returns are then computed per
    column, leaving zeros between releases — that is correct: the predictor
    "did not change" on those days, and OLS handles it natively.

    The per-stock fit later (refit.py) does a `dropna` against just the
    chosen predictors + the ticker, which is robust to NaN at row 0 etc.
    """
    stmt = select(Observation.variable_id, Observation.observed_on, Observation.value).where(
        Observation.variable_id.in_(variable_ids)
    )
    if start is not None:
        stmt = stmt.where(Observation.observed_on >= start)
    if end is not None:
        stmt = stmt.where(Observation.observed_on <= end)
    stmt = stmt.order_by(Observation.observed_on.asc())

    rows = (await session.execute(stmt)).all()
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["variable_id", "observed_on", "value"])
    df["value"] = df["value"].astype(float)
    wide = df.pivot(index="observed_on", columns="variable_id", values="value")
    wide.index = pd.to_datetime(wide.index)

    # Reindex onto a calendar that includes both business days *and* the
    # original observation dates, ffill, then drop back to business days.
    # This handles monthly series whose release date lands on a weekend
    # (e.g. CFNAI's nominal 1st-of-month timestamp can be a Sunday — bday-
    # only reindex would discard that observation entirely).
    bdays = pd.bdate_range(wide.index.min(), wide.index.max())
    combined = wide.index.union(bdays).sort_values()
    wide = wide.reindex(combined).ffill()
    wide = wide.loc[wide.index.isin(bdays)]

    # Log returns column-by-column. Series with non-positive values (e.g.
    # the 10Y-2Y spread can go negative) get a level diff instead so we
    # don't take log of negatives.
    returns = pd.DataFrame(index=wide.index)
    for col in wide.columns:
        s = wide[col].astype(float)
        if s.dropna().shape[0] < 2:
            continue
        if (s.dropna() <= 0).any():
            returns[col] = s.diff()
        else:
            returns[col] = np.log(s).diff()
    return returns.dropna(how="all")


async def load_active_model(session: AsyncSession, ticker: str) -> ModelFit | None:
    stmt = (
        select(ModelFit)
        .where(ModelFit.ticker == ticker, ModelFit.is_active.is_(True))
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_tickers(session: AsyncSession) -> list[str]:
    stmt = (
        select(Variable.id)
        .where(Variable.kind == "stock", Variable.active.is_(True))
        .order_by(Variable.id)
    )
    return [row[0] for row in (await session.execute(stmt)).all()]


async def list_predictor_ids(session: AsyncSession) -> list[str]:
    stmt = (
        select(Variable.id)
        .where(Variable.kind == "predictor", Variable.active.is_(True))
        .order_by(Variable.id)
    )
    return [row[0] for row in (await session.execute(stmt)).all()]


async def latest_price(session: AsyncSession, ticker: str) -> tuple[date, float] | None:
    """Most recent observation for a ticker — (date, level price)."""
    stmt = (
        select(Observation.observed_on, Observation.value)
        .where(Observation.variable_id == ticker)
        .order_by(Observation.observed_on.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    return row[0], float(row[1])


async def save_active_model(
    session: AsyncSession,
    *,
    ticker: str,
    training_start: date,
    training_end: date,
    diag: dict,
    predictor_ids: list[str],
) -> ModelFit:
    """Insert a new ModelFit row and atomically swap ``is_active`` over to it.

    The unique partial index ``uniq_active_model`` enforces "≤1 active per
    ticker" — we deactivate first to avoid a unique-violation race.
    """
    await session.execute(
        update(ModelFit)
        .where(ModelFit.ticker == ticker, ModelFit.is_active.is_(True))
        .values(is_active=False)
    )

    fit = ModelFit(
        id=uuid.uuid4(),
        ticker=ticker,
        training_start=training_start,
        training_end=training_end,
        n_obs=int(diag["n_obs"]),
        predictor_ids=predictor_ids,
        intercept=float(diag["intercept"]),
        coefficients=diag["coefficients"],
        r2=float(diag["r2"]),
        r2_adj=float(diag["r2_adj"]),
        durbin_watson=float(diag["durbin_watson"]),
        breusch_pagan_p=float(diag["breusch_pagan_p"]),
        max_vif=float(diag["max_vif"]),
        status=diag["status"],
        is_active=(diag["status"] == "PASS"),
    )
    session.add(fit)
    await session.flush()
    return fit


def lookback_window(end: date, days: int = 540) -> date:
    """Default training window: ~2 years of business days."""
    return end - timedelta(days=days)


__all__ = [
    "latest_price",
    "list_predictor_ids",
    "list_tickers",
    "load_active_model",
    "load_returns_frame",
    "lookback_window",
    "save_active_model",
]
