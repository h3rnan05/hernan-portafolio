"""Tests for the prediction pure functions."""

from __future__ import annotations

import math
from datetime import date

import pytest

from app.modeling.prediction import predict_next_return, prediction_record


def test_predict_next_return_simple() -> None:
    r = predict_next_return(
        intercept=0.001,
        coefficients={"x0": 0.5, "x1": -0.2},
        lagged_returns={"x0": 0.02, "x1": -0.01},
    )
    # 0.001 + 0.5*0.02 + (-0.2)*(-0.01) = 0.013
    assert r == pytest.approx(0.013)


def test_missing_predictor_raises() -> None:
    with pytest.raises(KeyError):
        predict_next_return(0.0, {"x0": 1.0}, {})


def test_prediction_record_uses_log_return() -> None:
    rec = prediction_record(
        ticker="NVDA",
        predicted_for=date(2026, 5, 5),
        last_price=100.0,
        intercept=0.0,
        coefficients={"x": 0.5},
        lagged_returns={"x": 0.02},  # → predicted log-return = 0.01
    )
    assert rec["predicted_return"] == pytest.approx(0.01)
    assert rec["predicted_price"] == pytest.approx(100.0 * math.exp(0.01))
    assert rec["ticker"] == "NVDA"
    assert rec["predicted_for"] == date(2026, 5, 5)


def test_negative_price_rejected() -> None:
    with pytest.raises(ValueError):
        prediction_record(
            ticker="X",
            predicted_for=date(2026, 1, 1),
            last_price=-1.0,
            intercept=0.0,
            coefficients={"x": 0.0},
            lagged_returns={"x": 0.0},
        )
