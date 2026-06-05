"""Tests for the price-forecast core (HER-17)."""

from __future__ import annotations

import math
from datetime import date
from itertools import pairwise

import pytest

from app.modeling.forecast import Z_90, build_forecast


def test_band_always_widens() -> None:
    pts = build_forecast(
        last_price=100.0,
        intercept=0.0,
        day1_return=0.01,
        sigma_daily=0.02,
        horizon=5,
        as_of=date(2026, 6, 4),
    )
    widths = [p.upper - p.lower for p in pts]
    # Strictly increasing — day 5 must be wider than day 1 (the honesty rule).
    assert all(b > a for a, b in pairwise(widths))
    assert pts[-1].upper - pts[-1].lower > pts[0].upper - pts[0].lower


def test_central_path_day1_and_drift() -> None:
    pts = build_forecast(
        last_price=100.0,
        intercept=0.001,
        day1_return=0.02,
        sigma_daily=0.01,
        horizon=3,
        as_of=date(2026, 6, 4),
    )
    # Day 1 uses the full signal; later days drift at the intercept.
    assert pts[0].central == pytest.approx(100.0 * math.exp(0.02))
    assert pts[1].central == pytest.approx(100.0 * math.exp(0.02 + 0.001))
    assert pts[2].central == pytest.approx(100.0 * math.exp(0.02 + 2 * 0.001))


def test_band_matches_sqrt_t_diffusion() -> None:
    pts = build_forecast(
        last_price=50.0,
        intercept=0.0,
        day1_return=0.0,
        sigma_daily=0.03,
        horizon=4,
        as_of=date(2026, 6, 4),
    )
    for t, p in enumerate(pts, start=1):
        expected_upper = 50.0 * math.exp(Z_90 * 0.03 * math.sqrt(t))
        assert p.upper == pytest.approx(expected_upper)


def test_dates_are_business_days() -> None:
    # 2026-06-05 is a Friday → next 3 business days skip the weekend.
    pts = build_forecast(
        last_price=10.0,
        intercept=0.0,
        day1_return=0.0,
        sigma_daily=0.01,
        horizon=3,
        as_of=date(2026, 6, 5),
    )
    assert [p.on for p in pts] == [date(2026, 6, 8), date(2026, 6, 9), date(2026, 6, 10)]


def test_invalid_inputs_raise() -> None:
    with pytest.raises(ValueError):
        build_forecast(
            last_price=0.0, intercept=0.0, day1_return=0.0,
            sigma_daily=0.01, horizon=5, as_of=date(2026, 6, 4),
        )
    with pytest.raises(ValueError):
        build_forecast(
            last_price=10.0, intercept=0.0, day1_return=0.0,
            sigma_daily=0.01, horizon=0, as_of=date(2026, 6, 4),
        )
