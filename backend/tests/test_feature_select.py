"""Tests for the no-overlap greedy feature selector."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.modeling.feature_select import select_features_greedy

RNG = np.random.default_rng(7)


def _make_returns(n: int = 200) -> pd.DataFrame:
    """Two stocks A & B; predictor pA correlates strongly with A, pB with B."""
    pA = pd.Series(RNG.normal(0, 1, size=n))
    pB = pd.Series(RNG.normal(0, 1, size=n))
    pC = pd.Series(RNG.normal(0, 1, size=n))  # noise
    pD = pd.Series(RNG.normal(0, 1, size=n))  # noise
    A = 0.9 * pA.shift(1).fillna(0) + 0.05 * RNG.normal(0, 1, size=n)
    B = 0.9 * pB.shift(1).fillna(0) + 0.05 * RNG.normal(0, 1, size=n)
    return pd.DataFrame({"A": A, "B": B, "pA": pA, "pB": pB, "pC": pC, "pD": pD})


def test_no_predictor_used_twice() -> None:
    df = _make_returns()
    chosen = select_features_greedy(
        df,
        tickers=["A", "B"],
        predictors=["pA", "pB", "pC", "pD"],
        k_per_stock=2,
        k_min=2,
    )
    used = [p for picks in chosen.values() for p in picks]
    assert len(used) == len(set(used)), f"duplicates in {chosen}"


def test_strongest_signal_picked_first() -> None:
    df = _make_returns()
    chosen = select_features_greedy(
        df,
        tickers=["A", "B"],
        predictors=["pA", "pB", "pC", "pD"],
        k_per_stock=1,
        k_min=1,
    )
    # A should grab pA (its strongest), B should grab pB next
    assert chosen["A"][0] == "pA"
    assert chosen["B"][0] == "pB"


def test_unknown_column_raises() -> None:
    df = _make_returns()
    with pytest.raises(ValueError):
        select_features_greedy(
            df,
            tickers=["A"],
            predictors=["pA", "doesnt_exist"],
            k_per_stock=1,
            k_min=1,
        )


def test_k_outside_band_raises() -> None:
    df = _make_returns()
    with pytest.raises(ValueError):
        select_features_greedy(
            df,
            tickers=["A"],
            predictors=["pA"],
            k_per_stock=0,
            k_min=1,
            k_max=5,
        )


def test_runs_out_of_predictors_gracefully() -> None:
    """If we ask for more picks than predictors exist, return what we can."""
    df = _make_returns()
    chosen = select_features_greedy(
        df,
        tickers=["A", "B"],
        predictors=["pA", "pB"],  # only 2 predictors total
        k_per_stock=2,
        k_min=1,
        k_max=2,
    )
    # B should get whatever predictors A didn't use (might be 0 or 1)
    assert sum(len(v) for v in chosen.values()) <= 2
    assert chosen["A"]  # at least one
