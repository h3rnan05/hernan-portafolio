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
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

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


async def compute_forecast(
    session: AsyncSession, ticker: str, horizon: int = 5
) -> dict | None:
    """Build the full forecast for a ticker against the live DB.

    Shared by the `/models/{ticker}/forecast` endpoint and the AI assistant so
    the day-1-signal-then-drift logic and the σ-source fallback live in one
    place. Returns a JSON-friendly dict (dates as ISO strings), or ``None`` if
    the ticker has no active model / no price.
    """
    # Imported here to avoid a module-level cycle (data.py ← forecast.py).
    from app.modeling.data import (
        latest_price,
        load_active_model,
        load_returns_frame,
        load_variable_lags,
    )
    from app.modeling.prediction import predict_next_return

    m = await load_active_model(session, ticker)
    if m is None:
        return None
    last = await latest_price(session, ticker)
    if last is None:
        return None
    as_of, last_price_value = last

    predictors = list(m.predictor_ids or [])
    coefs = {k: float(v) for k, v in (m.coefficients or {}).items()}
    intercept = float(m.intercept)
    lag_overrides = await load_variable_lags(session, predictors)
    max_lag = max([1, *lag_overrides.values()]) if predictors else 1

    start = as_of - timedelta(days=max(120, max_lag * 3 + 60))
    returns = await load_returns_frame(
        session, variable_ids=[ticker, *predictors], start=start, end=as_of
    )

    note: str | None = None
    day1_return = intercept  # baseline drift if predictors can't be read

    if predictors and not returns.empty:
        eligible = returns.index[returns.index <= pd.Timestamp(as_of)]
        n_elig = len(eligible)
        lagged: dict[str, float] = {}
        ok = True
        for p in predictors:
            pos = n_elig - lag_overrides.get(p, 1)
            if p not in returns.columns or pos < 0 or pos >= n_elig:
                ok = False
                break
            val = returns[p].iloc[pos]
            if pd.isna(val):
                ok = False
                break
            lagged[p] = float(val)
        if ok:
            day1_return = predict_next_return(intercept, coefs, lagged)
        else:
            note = "Predictores recientes incompletos; se muestra la deriva base."
    else:
        note = "Sin historial de predictores reciente; se muestra la deriva base."

    if m.resid_std is not None:
        sigma_daily = float(m.resid_std)
        sigma_source = "model_resid"
    else:
        series = returns[ticker].dropna() if ticker in returns.columns else pd.Series(dtype=float)
        sigma_daily = float(series.std()) if len(series) > 2 else 0.02
        sigma_source = "realized_vol"

    points = build_forecast(
        last_price=last_price_value,
        intercept=intercept,
        day1_return=day1_return,
        sigma_daily=sigma_daily,
        horizon=horizon,
        as_of=as_of,
        z=Z_90,
    )

    return {
        "ticker": ticker,
        "as_of": as_of.isoformat(),
        "last_price": last_price_value,
        "horizon": horizon,
        "confidence": 0.90,
        "direction": "up" if day1_return >= 0 else "down",
        "direction_symbol": "▲" if day1_return >= 0 else "▼",
        "expected_return_1d": day1_return,
        "sigma_daily": sigma_daily,
        "status": m.status,
        "estimator": m.estimator,
        "sigma_source": sigma_source,
        "points": [
            {
                "on": p.on.isoformat(),
                "day": p.day,
                "central": p.central,
                "lower": p.lower,
                "upper": p.upper,
            }
            for p in points
        ],
        "note": note,
    }
