"""Tests for the per-variable transform (HER-15).

The headline bug: a monthly macro series, forward-filled to daily and turned
into a *return*, is zero ~95% of days (one impulse per release). The 'level'
transform turns it into a persistent, informative daily regressor.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.modeling.data import apply_transform


def _ffilled_monthly(n_days: int = 252) -> pd.Series:
    """A monthly macro level, forward-filled to a daily business-day index."""
    idx = pd.bdate_range("2022-01-01", periods=n_days)
    s = pd.Series(np.nan, index=idx, dtype=float)
    # One fresh release roughly every 21 business days, then ffill.
    level = 100.0
    for i in range(0, n_days, 21):
        level += 1.5
        s.iloc[i] = level
    return s.ffill()


def test_return_on_positive_series_is_log_return() -> None:
    s = pd.Series([100.0, 101.0, 99.0])
    out = apply_transform(s, "return")
    assert out.iloc[1] == pytest.approx(np.log(101 / 100))
    assert out.iloc[2] == pytest.approx(np.log(99 / 101))


def test_return_on_nonpositive_series_is_level_diff() -> None:
    # A spread that goes negative cannot be log-transformed.
    s = pd.Series([0.5, -0.2, 0.1])
    out = apply_transform(s, "return")
    assert out.iloc[1] == pytest.approx(-0.7)
    assert out.iloc[2] == pytest.approx(0.3)


def test_level_is_persistent_not_mostly_zeros() -> None:
    """The core HER-15 fix: a forward-filled monthly level stays informative."""
    s = _ffilled_monthly()
    as_return = apply_transform(s, "return").dropna()
    as_level = apply_transform(s, "level").dropna()

    # As a return the column is ~all zeros (only release days move).
    zero_frac_return = (as_return.abs() < 1e-12).mean()
    assert zero_frac_return > 0.9

    # As a level it is non-constant and never all-zeros.
    assert as_level.nunique() > 5
    assert as_level.std() > 0


def test_surprise_is_release_impulse() -> None:
    s = _ffilled_monthly()
    out = apply_transform(s, "surprise").dropna()
    # Mostly zeros (between releases) with occasional non-zero jumps.
    assert (out.abs() < 1e-12).mean() > 0.9
    assert (out.abs() > 0).sum() >= 5
