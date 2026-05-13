"""Backtest helpers — replay predictions vs. realized prices.

The MAPE (mean absolute percentage error) is the primary scoring metric:
    MAPE = mean( |pred - actual| / actual )
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(slots=True)
class BacktestResult:
    ticker: str
    n: int
    mape: float
    bias: float  # mean(pred - actual) / mean(actual)


def compute_mape(predicted: pd.Series, actual: pd.Series) -> float:
    """MAPE on aligned series, NaN-safe (drops missing pairs first)."""
    df = pd.concat({"p": predicted, "a": actual}, axis=1).dropna()
    if df.empty or (df["a"] == 0).all():
        return float("nan")
    df = df[df["a"] != 0]
    if df.empty:
        return float("nan")
    return float(((df["p"] - df["a"]).abs() / df["a"].abs()).mean())


def replay_predictions(predictions: pd.DataFrame) -> list[BacktestResult]:
    """Group predictions by ticker, compute MAPE + bias against actuals.

    Args:
        predictions: columns must include
            ``ticker``, ``predicted_price``, ``actual_price``.
    """
    required = {"ticker", "predicted_price", "actual_price"}
    missing = required - set(predictions.columns)
    if missing:
        raise ValueError(f"predictions missing columns: {sorted(missing)}")

    out: list[BacktestResult] = []
    for tkr, group in predictions.groupby("ticker", sort=True):
        valid = group.dropna(subset=["actual_price"])
        if valid.empty:
            out.append(BacktestResult(ticker=tkr, n=0, mape=float("nan"), bias=float("nan")))
            continue

        mape = compute_mape(valid["predicted_price"], valid["actual_price"])
        a_mean = float(valid["actual_price"].abs().mean())
        bias = (
            float((valid["predicted_price"] - valid["actual_price"]).mean()) / a_mean
            if a_mean > 0
            else float("nan")
        )

        out.append(BacktestResult(ticker=tkr, n=len(valid), mape=mape, bias=bias))
    return out
