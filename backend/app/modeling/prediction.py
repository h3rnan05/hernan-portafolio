"""Daily prediction generation from a fitted model.

A Prediction record is computed as:
    predicted_return_t = α + Σ βᵢ · ret(predictor_i, t-1)
    predicted_price_t  = last_price * exp(predicted_return_t)
"""

from __future__ import annotations

import math
from datetime import date as DateType
from typing import TypedDict


class PredictionRecord(TypedDict):
    """Shape of a row inserted into ``predictions``."""

    ticker: str
    predicted_for: DateType
    predicted_return: float
    predicted_price: float


def predict_next_return(
    intercept: float,
    coefficients: dict[str, float],
    lagged_returns: dict[str, float],
) -> float:
    """Apply the linear model to one day's lagged predictor returns.

    Missing predictors (key absent from ``lagged_returns``) raise a clear
    KeyError — callers must ensure every coefficient has a corresponding
    return value or filter the model before predicting.
    """
    missing = [p for p in coefficients if p not in lagged_returns]
    if missing:
        raise KeyError(f"Missing lagged returns for predictors: {missing}")

    contribution = sum(beta * lagged_returns[p] for p, beta in coefficients.items())
    return intercept + contribution


def prediction_record(
    ticker: str,
    predicted_for: DateType,
    last_price: float,
    intercept: float,
    coefficients: dict[str, float],
    lagged_returns: dict[str, float],
) -> PredictionRecord:
    """One end-to-end prediction. Returns a dict ready for INSERT."""
    if last_price <= 0:
        raise ValueError(f"last_price must be > 0, got {last_price}")

    pred_ret = predict_next_return(intercept, coefficients, lagged_returns)
    pred_price = last_price * math.exp(pred_ret)

    return PredictionRecord(
        ticker=ticker,
        predicted_for=predicted_for,
        predicted_return=pred_ret,
        predicted_price=pred_price,
    )
