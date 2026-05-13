"""Tests for the backtest helpers."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from app.portfolio.backtest import compute_mape, replay_predictions


def test_compute_mape_basic() -> None:
    p = pd.Series([100, 102, 98])
    a = pd.Series([100, 100, 100])
    # |0|/100 + |2|/100 + |2|/100 = 0.04/3 ≈ 0.01333
    assert compute_mape(p, a) == pytest.approx(0.04 / 3)


def test_compute_mape_drops_nan_pairs() -> None:
    p = pd.Series([100, 102, None])
    a = pd.Series([100, None, 100])
    assert compute_mape(p, a) == pytest.approx(0.0)


def test_compute_mape_all_nan_returns_nan() -> None:
    p = pd.Series([None, None])
    a = pd.Series([None, None])
    assert math.isnan(compute_mape(p, a))


def test_replay_predictions_groups_by_ticker() -> None:
    df = pd.DataFrame(
        [
            {"ticker": "A", "predicted_price": 100.0, "actual_price": 99.0},
            {"ticker": "A", "predicted_price": 110.0, "actual_price": 100.0},
            {"ticker": "B", "predicted_price": 50.0, "actual_price": 50.0},
        ]
    )
    results = replay_predictions(df)
    by_ticker = {r.ticker: r for r in results}

    assert by_ticker["A"].n == 2
    # mean(|100-99|/99, |110-100|/100)
    assert by_ticker["A"].mape == pytest.approx(((1 / 99) + (10 / 100)) / 2)
    assert by_ticker["B"].mape == pytest.approx(0.0)


def test_replay_handles_missing_actuals() -> None:
    df = pd.DataFrame(
        [
            {"ticker": "A", "predicted_price": 100.0, "actual_price": None},
        ]
    )
    results = replay_predictions(df)
    assert results[0].n == 0
    assert math.isnan(results[0].mape)


def test_replay_missing_columns_raises() -> None:
    bad = pd.DataFrame({"ticker": ["A"], "predicted_price": [1.0]})
    with pytest.raises(ValueError):
        replay_predictions(bad)
