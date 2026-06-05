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
        allow_reuse=False,  # legacy no-overlap path
    )
    used = [p for picks in chosen.values() for p in picks]
    assert len(used) == len(set(used)), f"duplicates in {chosen}"


def _make_shared_factor_returns(n: int = 300) -> pd.DataFrame:
    """Two stocks that BOTH load the same predictor (shared factor)."""
    shared = pd.Series(RNG.normal(0, 1, size=n))  # e.g. the semiconductor factor
    noise = pd.Series(RNG.normal(0, 1, size=n))
    # Both A and B are driven mostly by yesterday's `shared` move.
    A = 0.9 * shared.shift(1).fillna(0) + 0.05 * RNG.normal(0, 1, size=n)
    B = 0.85 * shared.shift(1).fillna(0) + 0.05 * RNG.normal(0, 1, size=n)
    return pd.DataFrame({"A": A, "B": B, "shared": shared, "noise": noise})


def test_reuse_allows_shared_predictor() -> None:
    """HER-14: with allow_reuse=True both stocks may grab the same factor."""
    df = _make_shared_factor_returns()
    chosen = select_features_greedy(
        df,
        tickers=["A", "B"],
        predictors=["shared", "noise"],
        k_per_stock=1,
        k_min=1,
        k_max=1,
        allow_reuse=True,
    )
    assert chosen["A"] == ["shared"]
    assert chosen["B"] == ["shared"], "B should be free to reuse the shared factor"


def test_no_reuse_forces_second_stock_off_best_predictor() -> None:
    """Legacy path: B is denied `shared` once A claims it."""
    df = _make_shared_factor_returns()
    chosen = select_features_greedy(
        df,
        tickers=["A", "B"],
        predictors=["shared", "noise"],
        k_per_stock=1,
        k_min=1,
        k_max=1,
        allow_reuse=False,
    )
    assert chosen["A"] == ["shared"]
    assert chosen["B"] == ["noise"], "no-reuse forces B onto an inferior predictor"


def test_per_predictor_lag_override() -> None:
    """HER-15: a predictor with its own lag is correlated at that lag."""
    n = 300
    p_fast = pd.Series(RNG.normal(0, 1, size=n))
    # Stock reacts to p_fast with a 5-day delay, not 1.
    stock = 0.9 * p_fast.shift(5).fillna(0) + 0.05 * RNG.normal(0, 1, size=n)
    df = pd.DataFrame({"S": stock, "p_fast": p_fast, "noise": RNG.normal(0, 1, size=n)})
    # With the right lag override the signal is found; with lag=1 it's buried.
    chosen = select_features_greedy(
        df,
        tickers=["S"],
        predictors=["p_fast", "noise"],
        k_per_stock=1,
        k_min=1,
        k_max=1,
        lag_days=1,
        lag_overrides={"p_fast": 5},
    )
    assert chosen["S"] == ["p_fast"]


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
    """No-reuse path: if we run out of unused predictors, return what we can."""
    df = _make_returns()
    chosen = select_features_greedy(
        df,
        tickers=["A", "B"],
        predictors=["pA", "pB"],  # only 2 predictors total
        k_per_stock=2,
        k_min=1,
        k_max=2,
        allow_reuse=False,  # exhaustion only happens under no-overlap
    )
    # B should get whatever predictors A didn't use (might be 0 or 1)
    assert sum(len(v) for v in chosen.values()) <= 2
    assert chosen["A"]  # at least one
