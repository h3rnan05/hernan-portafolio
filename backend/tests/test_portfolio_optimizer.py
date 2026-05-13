"""Tests for the portfolio optimizer."""

from __future__ import annotations

import pandas as pd
import pytest

from app.portfolio.optimizer import (
    PROFILE_IDS,
    blend,
    build_portfolios,
    validate_weights,
)


def _make_metrics() -> pd.DataFrame:
    """Three stocks with diverging vol/sharpe so all 5 profiles diverge."""
    return pd.DataFrame(
        {
            "r2": [0.95, 0.91, 0.93],
            "pred_ret_30d": [0.05, 0.02, 0.10],
            "vol_annual": [0.10, 0.20, 0.40],  # SAFE → MID → RISKY
            "sharpe": [1.5, 0.5, 1.0],
        },
        index=["SAFE", "MID", "RISKY"],
    )


def test_all_five_profiles_built() -> None:
    metrics = _make_metrics()
    profiles = build_portfolios(metrics)
    assert set(profiles.keys()) == set(PROFILE_IDS)


def test_all_weights_sum_to_one() -> None:
    metrics = _make_metrics()
    profiles = build_portfolios(metrics)
    for w in profiles.values():
        validate_weights(w)


def test_balanced_is_equal_weight() -> None:
    metrics = _make_metrics()
    profiles = build_portfolios(metrics)
    weights = profiles["P3_BALANCED"]
    assert all(v == pytest.approx(1.0 / 3) for v in weights.values())


def test_conservative_prefers_low_vol() -> None:
    metrics = _make_metrics()
    profiles = build_portfolios(metrics)
    p1 = profiles["P1_CONSERVATIVE"]
    assert p1["SAFE"] > p1["MID"] > p1["RISKY"]


def test_aggressive_prefers_high_sharpe() -> None:
    metrics = _make_metrics()
    profiles = build_portfolios(metrics)
    p5 = profiles["P5_AGGRESSIVE"]
    # SAFE has the highest Sharpe and highest return-rank → highest weight.
    assert p5["SAFE"] >= p5["RISKY"]


def test_blend_normalizes() -> None:
    a = {"x": 0.6, "y": 0.4}
    b = {"x": 0.2, "y": 0.8}
    blended = blend(a, b, 0.5)
    assert blended["x"] == pytest.approx(0.4)
    assert blended["y"] == pytest.approx(0.6)
    validate_weights(blended)


def test_blend_t_out_of_range() -> None:
    with pytest.raises(ValueError):
        blend({"x": 1.0}, {"x": 1.0}, 1.5)


def test_missing_columns_raise() -> None:
    bad = pd.DataFrame({"r2": [0.9], "vol_annual": [0.1]}, index=["A"])
    with pytest.raises(ValueError):
        build_portfolios(bad)


def test_empty_metrics_raises() -> None:
    with pytest.raises(ValueError):
        build_portfolios(pd.DataFrame(columns=["r2", "pred_ret_30d", "vol_annual", "sharpe"]))


def test_zero_signal_falls_back_to_equal_weight() -> None:
    """If every Sharpe is 0 the aggressive weight collapses to equal weight."""
    metrics = pd.DataFrame(
        {
            "r2": [0.9, 0.9],
            "pred_ret_30d": [0.0, 0.0],
            "vol_annual": [0.1, 0.1],
            "sharpe": [0.0, 0.0],
        },
        index=["A", "B"],
    )
    profiles = build_portfolios(metrics)
    p5 = profiles["P5_AGGRESSIVE"]
    assert p5["A"] == pytest.approx(0.5)
    assert p5["B"] == pytest.approx(0.5)
