"""Refit-all driver — fits one model per stock and persists the result.

Designed to be called from the weekly cron, an admin endpoint, or the
``scripts/refit_all.py`` CLI.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.modeling.data import (
    list_predictor_ids,
    list_tickers,
    load_returns_frame,
    lookback_window,
    save_active_model,
)
from app.modeling.feature_select import select_features_greedy
from app.modeling.regression import fit_and_diagnose

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class RefitOutcome:
    ticker: str
    status: str  # 'PASS' | 'REVIEW' | 'SKIPPED'
    r2: float | None
    n_obs: int | None
    predictor_ids: list[str]
    error: str | None = None


async def refit_all(
    session: AsyncSession,
    *,
    end: date | None = None,
    lookback_days: int = 540,
    k_per_stock: int = 4,
    lag_days: int = 1,
    min_obs: int = 60,
) -> list[RefitOutcome]:
    """Refit every active stock against every active predictor.

    The function is idempotent — re-running with the same window simply
    inserts another ModelFit row and swaps ``is_active``.
    """
    end = end or date.today()
    start = lookback_window(end, lookback_days)

    tickers = await list_tickers(session)
    predictors = await list_predictor_ids(session)

    if not tickers or not predictors:
        log.warning("refit_no_variables", n_tickers=len(tickers), n_predictors=len(predictors))
        return []

    returns = await load_returns_frame(
        session,
        variable_ids=tickers + predictors,
        start=start,
        end=end,
    )

    outcomes: list[RefitOutcome] = []

    if returns.empty:
        log.warning("refit_no_returns", start=start, end=end)
        return [
            RefitOutcome(
                ticker=t,
                status="SKIPPED",
                r2=None,
                n_obs=0,
                predictor_ids=[],
                error="no observations in window",
            )
            for t in tickers
        ]

    # Restrict to columns we actually have data for
    available_tickers = [t for t in tickers if t in returns.columns]
    available_predictors = [p for p in predictors if p in returns.columns]

    chosen = select_features_greedy(
        returns,
        tickers=available_tickers,
        predictors=available_predictors,
        lag_days=lag_days,
        k_per_stock=k_per_stock,
    )

    for tkr in tickers:
        if tkr not in chosen or not chosen[tkr]:
            outcomes.append(
                RefitOutcome(tkr, "SKIPPED", None, 0, [], "no predictors selected")
            )
            continue

        picks = chosen[tkr]
        # Build aligned y/X (lagged predictors vs. contemporaneous y)
        lagged = returns[picks].shift(lag_days)
        aligned = returns[[tkr]].join(lagged, how="inner").dropna()

        if aligned.shape[0] < min_obs:
            outcomes.append(
                RefitOutcome(tkr, "SKIPPED", None, aligned.shape[0], picks, "n_obs<min")
            )
            continue

        y = aligned[tkr]
        X = aligned[picks]

        try:
            diag = fit_and_diagnose(y, X)
        except Exception as e:
            log.error("refit_fit_failed", ticker=tkr, err=str(e))
            outcomes.append(RefitOutcome(tkr, "SKIPPED", None, aligned.shape[0], picks, str(e)))
            continue

        await save_active_model(
            session,
            ticker=tkr,
            training_start=aligned.index.min().date(),
            training_end=aligned.index.max().date(),
            diag=diag,
            predictor_ids=picks,
        )
        outcomes.append(
            RefitOutcome(
                ticker=tkr,
                status=diag["status"],
                r2=diag["r2"],
                n_obs=diag["n_obs"],
                predictor_ids=picks,
            )
        )
        log.info(
            "refit_ok",
            ticker=tkr,
            r2=round(diag["r2"], 3),
            dw=round(diag["durbin_watson"], 3),
            bp_p=round(diag["breusch_pagan_p"], 3),
            max_vif=round(diag["max_vif"], 3),
            status=diag["status"],
        )

    await session.commit()
    return outcomes
