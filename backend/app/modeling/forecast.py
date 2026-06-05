"""Price forecast with an honest, widening confidence band — HER-17.

The model only has a **one-day-ahead** signal: it predicts tomorrow's return
from today's lagged predictors. Projecting that single edge N days forward as a
constant trend would compound a one-day bet into an N-day move and overstate it.
So the central path is honest about what the model actually knows:

    day 1   : use the full model prediction (intercept + Σβ·last_predictors)
    day 2..N: drift at the baseline (the intercept) — we don't know the
              predictors' future values, so their expected contribution is ~0.

    central_t = last_price · exp( r₁ + (t-1)·intercept )

The band is a random-walk diffusion that **must widen** with the horizon:

    log price_t ~ Normal( log(central_t),  σ²·t )
    band_t = z · σ · √t          (z = 1.6449 ≈ 90%)
    lower/upper_t = central_t · exp( ∓ band_t )

σ is the model's residual standard error (the 1-day return sigma). The deeper
honest move would be to use the HER-13 out-of-sample RMSE here; the frontend
already shows that hit rate next to the forecast so the user can judge it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date as DateType
from datetime import timedelta

# z-score for a two-sided 90% interval.
Z_90 = 1.6449


@dataclass(slots=True)
class ForecastPoint:
    on: DateType
    day: int  # 1..horizon
    central: float
    lower: float
    upper: float


def _business_days_after(start: DateType, n: int) -> list[DateType]:
    """The next ``n`` business days strictly after ``start`` (skips weekends)."""
    out: list[DateType] = []
    d = start
    while len(out) < n:
        d = d + timedelta(days=1)
        if d.weekday() < 5:  # Mon-Fri
            out.append(d)
    return out


def build_forecast(
    *,
    last_price: float,
    intercept: float,
    day1_return: float,
    sigma_daily: float,
    horizon: int,
    as_of: DateType,
    z: float = Z_90,
) -> list[ForecastPoint]:
    """Project the price path + widening band. Pure and unit-testable.

    ``day1_return`` is the model's one-step prediction; days 2..N drift at
    ``intercept``. ``sigma_daily`` is the per-day return sigma.
    """
    if last_price <= 0:
        raise ValueError(f"last_price must be > 0, got {last_price}")
    if horizon < 1:
        raise ValueError("horizon must be ≥ 1")

    dates = _business_days_after(as_of, horizon)
    points: list[ForecastPoint] = []
    for t in range(1, horizon + 1):
        mu_cum = day1_return + (t - 1) * intercept
        central = last_price * math.exp(mu_cum)
        band = z * sigma_daily * math.sqrt(t)
        points.append(
            ForecastPoint(
                on=dates[t - 1],
                day=t,
                central=central,
                lower=last_price * math.exp(mu_cum - band),
                upper=last_price * math.exp(mu_cum + band),
            )
        )
    return points
