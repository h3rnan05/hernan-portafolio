"""Walk-forward (out-of-sample) validation — HER-13.

The diagnostics in ``regression.py`` (R², DW, BP, VIF) are **in-sample**: they
describe how well the model fits the data it was trained on, which says almost
nothing about predictive skill. Before showing a forecast to an investor we
need numbers earned on days the model never saw.

Walk-forward, mirroring production:
    1. Take a rolling training window (default 252 business days).
    2. **Select predictors inside that window** and fit the model.
    3. Freeze the model and predict the next ``step`` days (default 5 — the
       system refits weekly and predicts daily off the frozen model, so the
       validation must too).
    4. Record (predicted_return, actual_return) for each out-of-sample day.
    5. Slide the window forward by ``step`` and repeat.

Anti-leakage rules baked in (these are where walk-forward backtests lie):
    * Feature selection runs **inside each training window** — never once on
      the full history. Selecting on all data then rolling leaks the future
      into the predictor choice and inflates every metric.
    * A day t is predicted using only data through t-1 (lagged predictors).
    * Standardization for ridge/lasso happens per-fit (inside fit_and_diagnose)
      on the training window only.

Reported metrics (what actually matters):
    * directional hit rate, with the **up-day base rate** for honest comparison
      and a one-sided binomial p-value (is the edge real or noise?);
    * out-of-sample RMSE / MAE of the predicted return;
    * Sharpe of a long/flat strategy **net of transaction cost** vs buy-and-hold;
    * cumulative-return curves for both.

The pure engine ``run_walk_forward_frame`` takes a returns DataFrame so it can
be tested on synthetic data with a known answer; ``walk_forward`` is the thin
DB wrapper.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as DateType

import numpy as np
import pandas as pd
import structlog
from scipy.stats import binomtest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import K_PER_STOCK
from app.modeling.data import (
    list_predictor_ids,
    load_returns_frame,
    lookback_window,
)
from app.modeling.feature_select import select_features_greedy
from app.modeling.prediction import predict_next_return
from app.modeling.regression import fit_and_diagnose

log = structlog.get_logger(__name__)

TRADING_DAYS = 252
# Minimum non-NaN return rows for a predictor to be eligible inside a window.
_MIN_NON_NAN = 30


@dataclass(slots=True)
class CurvePoint:
    on: DateType
    strategy: float  # cumulative return of the long/flat strategy
    buy_hold: float  # cumulative return of buy-and-hold


@dataclass(slots=True)
class WalkForwardResult:
    ticker: str
    estimator: str
    train_window: int
    step: int
    n_windows: int
    n_predictions: int
    # Directional skill
    hit_rate: float | None
    up_day_base_rate: float | None
    edge_vs_base: float | None  # hit_rate - max(0.5, base_rate)
    hit_rate_pvalue: float | None  # one-sided binomial vs 0.5
    significant: bool  # pvalue < 0.05 AND hit_rate > base_rate
    # Error of the predicted return
    rmse: float | None
    mae: float | None
    # Strategy (net of cost) vs buy-and-hold
    sharpe_strategy: float | None
    sharpe_buy_hold: float | None
    total_return_strategy: float | None
    total_return_buy_hold: float | None
    cost_bps: float
    curve: list[CurvePoint] = field(default_factory=list)
    note: str | None = None


def _sharpe(r: np.ndarray) -> float:
    """Annualized Sharpe of a daily return series (zero risk-free)."""
    sd = r.std(ddof=1) if len(r) > 1 else 0.0
    if sd <= 0:
        return 0.0
    return float(r.mean() / sd * np.sqrt(TRADING_DAYS))


def _metrics(
    *,
    ticker: str,
    estimator: str,
    train_window: int,
    step: int,
    n_windows: int,
    dates: list[DateType],
    pred: np.ndarray,
    actual: np.ndarray,
    cost_bps: float,
) -> WalkForwardResult:
    """Compute all OOS metrics from aligned predicted/actual return arrays.

    Pure and side-effect free so it can be unit-tested directly.
    """
    n = len(pred)
    empty = WalkForwardResult(
        ticker=ticker,
        estimator=estimator,
        train_window=train_window,
        step=step,
        n_windows=n_windows,
        n_predictions=n,
        hit_rate=None,
        up_day_base_rate=None,
        edge_vs_base=None,
        hit_rate_pvalue=None,
        significant=False,
        rmse=None,
        mae=None,
        sharpe_strategy=None,
        sharpe_buy_hold=None,
        total_return_strategy=None,
        total_return_buy_hold=None,
        cost_bps=cost_bps,
    )
    if n == 0:
        empty.note = "no out-of-sample predictions produced"
        return empty

    # Directional accuracy — exclude days where either side is exactly zero.
    nonzero = (pred != 0) & (actual != 0)
    n_eff = int(nonzero.sum())
    if n_eff > 0:
        hits = np.sign(pred[nonzero]) == np.sign(actual[nonzero])
        n_hits = int(hits.sum())
        hit_rate = n_hits / n_eff
        # One-sided binomial: is the model better than a coin flip?
        pvalue = float(binomtest(n_hits, n_eff, 0.5, alternative="greater").pvalue)
    else:
        hit_rate = None
        pvalue = None

    # The honest benchmark is the unconditional up-day frequency, not 50%.
    base_rate = float((actual > 0).mean())
    bench = max(0.5, base_rate)
    edge = (hit_rate - bench) if hit_rate is not None else None
    significant = bool(
        hit_rate is not None
        and pvalue is not None
        and pvalue < 0.05
        and hit_rate > base_rate
    )

    err = pred - actual
    rmse = float(np.sqrt(np.mean(err**2)))
    mae = float(np.mean(np.abs(err)))

    # Long/flat strategy: hold the stock when the model predicts a positive
    # return, otherwise sit in cash. Charge `cost_bps` on every change in
    # position (entering or exiting), so a signal that flips constantly is
    # penalized — the most over-sold number in backtesting is a cost-free Sharpe.
    pos = (pred > 0).astype(float)
    prev = np.concatenate([[0.0], pos[:-1]])
    turnover = np.abs(pos - prev)
    cost = cost_bps / 1e4
    # Returns are log returns, so cumulative = exp(cumsum). Subtracting the cost
    # in log space is the standard small-cost approximation.
    strat_ret = pos * actual - turnover * cost
    bh_ret = actual

    cum_strat = np.exp(np.cumsum(strat_ret)) - 1.0
    cum_bh = np.exp(np.cumsum(bh_ret)) - 1.0
    curve = [
        CurvePoint(on=dates[i], strategy=float(cum_strat[i]), buy_hold=float(cum_bh[i]))
        for i in range(n)
    ]

    return WalkForwardResult(
        ticker=ticker,
        estimator=estimator,
        train_window=train_window,
        step=step,
        n_windows=n_windows,
        n_predictions=n,
        hit_rate=hit_rate,
        up_day_base_rate=base_rate,
        edge_vs_base=edge,
        hit_rate_pvalue=pvalue,
        significant=significant,
        rmse=rmse,
        mae=mae,
        sharpe_strategy=_sharpe(strat_ret),
        sharpe_buy_hold=_sharpe(bh_ret),
        total_return_strategy=float(cum_strat[-1]),
        total_return_buy_hold=float(cum_bh[-1]),
        cost_bps=cost_bps,
        curve=curve,
    )


def run_walk_forward_frame(
    returns: pd.DataFrame,
    ticker: str,
    predictors: list[str],
    *,
    train_window: int = TRADING_DAYS,
    step: int = 5,
    k_per_stock: int = K_PER_STOCK,
    lag_days: int = 1,
    lag_overrides: dict[str, int] | None = None,
    allow_reuse: bool = True,
    estimator: str = "ols",
    alpha: float | None = None,
    min_obs: int = 60,
    cost_bps: float = 1.0,
) -> WalkForwardResult:
    """Pure walk-forward engine over a returns DataFrame (no DB).

    ``returns`` columns must include ``ticker`` and every id in ``predictors``,
    all as return (or transform) series sharing one ascending index.
    """
    overrides = lag_overrides or {}

    def _lag(p: str) -> int:
        return overrides.get(p, lag_days)

    if ticker not in returns.columns:
        return _empty_result(ticker, estimator, train_window, step, f"no data for {ticker}")

    present = [p for p in predictors if p in returns.columns]
    idx = returns.index
    n_rows = len(idx)
    if n_rows <= train_window + 1:
        return _empty_result(
            ticker, estimator, train_window, step,
            f"history too short: {n_rows} rows ≤ train_window {train_window}",
        )

    # Pre-lag every predictor once (per-predictor lag); slicing this frame for
    # both fitting and OOS prediction guarantees identical lag handling.
    lagged_all = pd.DataFrame(index=idx)
    for p in present:
        lagged_all[p] = returns[p].shift(_lag(p))

    y_all = returns[ticker]

    dates: list[DateType] = []
    pred_list: list[float] = []
    actual_list: list[float] = []
    n_windows = 0

    for te in range(train_window, n_rows, step):
        train_start = te - train_window
        window = returns.iloc[train_start:te]

        # Predictors with enough data *inside this window* (mirrors refit.py).
        eligible = [p for p in present if window[p].notna().sum() >= _MIN_NON_NAN]
        if not eligible:
            continue

        # Feature selection INSIDE the window — never on the full history.
        chosen = select_features_greedy(
            window,
            tickers=[ticker],
            predictors=eligible,
            lag_days=lag_days,
            lag_overrides=overrides,
            k_per_stock=k_per_stock,
            allow_reuse=allow_reuse,
        )
        picks = chosen.get(ticker, [])
        if not picks:
            continue

        # Training design: lagged predictors vs contemporaneous y, in-window.
        train_df = lagged_all[picks].iloc[train_start:te].copy()
        train_df[ticker] = y_all.iloc[train_start:te]
        train_df = train_df.dropna()
        if len(train_df) < min_obs:
            continue

        try:
            diag = fit_and_diagnose(
                train_df[ticker], train_df[picks], estimator=estimator, alpha=alpha
            )
        except Exception as e:  # pragma: no cover - defensive
            log.warning("wf_fit_failed", ticker=ticker, te=int(te), err=str(e))
            continue

        n_windows += 1
        intercept = diag["intercept"]
        coefs = diag["coefficients"]

        # Predict the next `step` days with the frozen model.
        for t_pos in range(te, min(te + step, n_rows)):
            xrow = lagged_all[picks].iloc[t_pos]
            if xrow.isna().any():
                continue
            actual_ret = y_all.iloc[t_pos]
            if pd.isna(actual_ret):
                continue
            pred_ret = predict_next_return(
                intercept, coefs, {p: float(xrow[p]) for p in picks}
            )
            dates.append(idx[t_pos].date() if hasattr(idx[t_pos], "date") else idx[t_pos])
            pred_list.append(float(pred_ret))
            actual_list.append(float(actual_ret))

    return _metrics(
        ticker=ticker,
        estimator=estimator,
        train_window=train_window,
        step=step,
        n_windows=n_windows,
        dates=dates,
        pred=np.asarray(pred_list, dtype=float),
        actual=np.asarray(actual_list, dtype=float),
        cost_bps=cost_bps,
    )


def _empty_result(
    ticker: str, estimator: str, train_window: int, step: int, note: str
) -> WalkForwardResult:
    return WalkForwardResult(
        ticker=ticker,
        estimator=estimator,
        train_window=train_window,
        step=step,
        n_windows=0,
        n_predictions=0,
        hit_rate=None,
        up_day_base_rate=None,
        edge_vs_base=None,
        hit_rate_pvalue=None,
        significant=False,
        rmse=None,
        mae=None,
        sharpe_strategy=None,
        sharpe_buy_hold=None,
        total_return_strategy=None,
        total_return_buy_hold=None,
        cost_bps=0.0,
        note=note,
    )


async def walk_forward(
    session: AsyncSession,
    ticker: str,
    *,
    train_window: int = TRADING_DAYS,
    step: int = 5,
    k_per_stock: int = K_PER_STOCK,
    lag_days: int = 1,
    allow_reuse: bool = True,
    estimator: str = "ols",
    alpha: float | None = None,
    min_obs: int = 60,
    cost_bps: float = 1.0,
    history_days: int = 1500,
) -> WalkForwardResult:
    """DB wrapper: load the ticker + all predictors and run the walk-forward.

    ``history_days`` bounds how far back we pull (default ~6y of calendar days);
    the walk-forward then rolls a ``train_window``-day window across it.
    """
    predictors = await list_predictor_ids(session)
    end = None
    start = lookback_window(DateType.today(), history_days)
    returns = await load_returns_frame(
        session,
        variable_ids=[ticker, *predictors],
        start=start,
        end=end,
    )
    if returns.empty or ticker not in returns.columns:
        return _empty_result(
            ticker, estimator, train_window, step, "no observations for ticker"
        )

    return run_walk_forward_frame(
        returns,
        ticker,
        predictors,
        train_window=train_window,
        step=step,
        k_per_stock=k_per_stock,
        lag_days=lag_days,
        allow_reuse=allow_reuse,
        estimator=estimator,
        alpha=alpha,
        min_obs=min_obs,
        cost_bps=cost_bps,
    )
