"""Tests for the walk-forward validator (HER-13).

The synthetic designs have a *known* answer:
  * a genuine lag-1 signal must produce a high hit rate;
  * a purely contemporaneous relationship (no lagged signal) must produce a
    ~coin-flip hit rate — if it doesn't, the engine is leaking the future.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.modeling.validation import _metrics, run_walk_forward_frame

RNG = np.random.default_rng(11)


def _bdays(n: int) -> pd.DatetimeIndex:
    return pd.bdate_range("2018-01-01", periods=n)


# ─── pure metric function ────────────────────────────────────────────────────


def test_metrics_perfect_direction() -> None:
    n = 200
    dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
    actual = RNG.normal(0, 0.01, size=n)
    actual[actual == 0] = 0.01
    pred = actual.copy()  # perfect directional agreement
    res = _metrics(
        ticker="T",
        estimator="ols",
        train_window=10,
        step=5,
        n_windows=1,
        dates=dates,
        pred=pred,
        actual=actual,
        cost_bps=1.0,
    )
    assert res.hit_rate == 1.0
    assert res.significant is True
    assert res.sharpe_strategy is not None and res.sharpe_strategy > 0
    assert res.n_predictions == n


def test_metrics_coinflip_not_significant() -> None:
    n = 300
    dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
    actual = RNG.normal(0, 0.01, size=n)
    pred = RNG.normal(0, 0.01, size=n)  # independent of actual
    res = _metrics(
        ticker="T",
        estimator="ols",
        train_window=10,
        step=5,
        n_windows=1,
        dates=dates,
        pred=pred,
        actual=actual,
        cost_bps=1.0,
    )
    assert res.hit_rate is not None
    assert 0.4 <= res.hit_rate <= 0.6
    assert res.significant is False


def test_metrics_empty() -> None:
    res = _metrics(
        ticker="T",
        estimator="ols",
        train_window=10,
        step=5,
        n_windows=0,
        dates=[],
        pred=np.array([]),
        actual=np.array([]),
        cost_bps=1.0,
    )
    assert res.n_predictions == 0
    assert res.note is not None


# ─── walk-forward engine ─────────────────────────────────────────────────────


def test_walk_forward_recovers_lagged_signal() -> None:
    """ticker_t = 0.8·pred_{t-1} + noise → the model should predict direction."""
    n = 600
    idx = _bdays(n)
    pred = RNG.normal(0, 1, size=n)
    noise1 = RNG.normal(0, 1, size=n)
    noise2 = RNG.normal(0, 1, size=n)
    ticker = 0.8 * np.concatenate([[0.0], pred[:-1]]) + RNG.normal(0, 0.2, size=n)
    df = pd.DataFrame(
        {"AAA": ticker, "sig": pred, "n1": noise1, "n2": noise2}, index=idx
    )

    res = run_walk_forward_frame(
        df,
        "AAA",
        ["sig", "n1", "n2"],
        train_window=252,
        step=5,
        min_obs=60,
    )
    assert res.n_predictions > 0
    assert res.hit_rate is not None and res.hit_rate > 0.65
    assert res.significant is True
    # A real edge should beat buy-and-hold on a risk-adjusted basis.
    assert res.sharpe_strategy > res.sharpe_buy_hold


def test_walk_forward_no_leakage() -> None:
    """ticker depends on pred at the SAME day, not lagged.

    A correct (lagged) walk-forward has no signal to exploit → hit rate ≈ 0.5.
    A leaky implementation that peeked at the same-day predictor would score
    near 1.0, so this test fails loudly if leakage is ever introduced.
    """
    n = 600
    idx = _bdays(n)
    pred = RNG.normal(0, 1, size=n)
    ticker = 0.8 * pred + RNG.normal(0, 0.2, size=n)  # contemporaneous only
    df = pd.DataFrame(
        {"BBB": ticker, "sig": pred, "n1": RNG.normal(0, 1, size=n)}, index=idx
    )

    res = run_walk_forward_frame(
        df, "BBB", ["sig", "n1"], train_window=252, step=5, min_obs=60
    )
    assert res.n_predictions > 0
    assert res.hit_rate is not None
    assert 0.4 <= res.hit_rate <= 0.6, f"leakage suspected: hit_rate={res.hit_rate}"
    assert res.significant is False


def test_walk_forward_short_history() -> None:
    n = 100
    idx = _bdays(n)
    df = pd.DataFrame(
        {"CCC": RNG.normal(0, 1, size=n), "sig": RNG.normal(0, 1, size=n)}, index=idx
    )
    res = run_walk_forward_frame(df, "CCC", ["sig"], train_window=252, step=5)
    assert res.n_predictions == 0
    assert res.note is not None


def test_walk_forward_ridge_runs() -> None:
    """Ridge estimator path also produces OOS metrics on a lagged signal."""
    n = 500
    idx = _bdays(n)
    pred = RNG.normal(0, 1, size=n)
    ticker = 0.7 * np.concatenate([[0.0], pred[:-1]]) + RNG.normal(0, 0.3, size=n)
    df = pd.DataFrame(
        {"DDD": ticker, "sig": pred, "n1": RNG.normal(0, 1, size=n)}, index=idx
    )
    res = run_walk_forward_frame(
        df, "DDD", ["sig", "n1"], train_window=252, step=5, estimator="ridge"
    )
    assert res.estimator == "ridge"
    assert res.n_predictions > 0
    assert res.hit_rate is not None and res.hit_rate > 0.55
