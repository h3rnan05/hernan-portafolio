"""Model-fit endpoints: list, detail, refit (admin)."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.db import AsyncSessionLocal, get_session
from app.modeling.data import (
    latest_price,
    load_active_model,
    load_returns_frame,
    load_variable_lags,
)
from app.modeling.forecast import Z_90, build_forecast
from app.modeling.prediction import predict_next_return
from app.modeling.refit import refit_all
from app.modeling.regression import ESTIMATORS
from app.modeling.validation import walk_forward
from app.models import ModelFit, Observation
from app.schemas import (
    ForecastPoint,
    ForecastResult,
    ModelAudit,
    ModelDetail,
    ModelSummary,
    ObservationAudit,
    RefitOutcomeOut,
    RefitRequest,
    ValidationCurvePoint,
    ValidationResult,
)

router = APIRouter(prefix="/models", tags=["models"])


def _equation(intercept: float, coefficients: dict[str, float]) -> str:
    """Render the OLS equation as a single string for UI display."""
    parts = [f"{intercept:+.6f}"]
    for name, beta in coefficients.items():
        parts.append(f"{beta:+.6f}·{name}_t-1")
    return "ret_t = " + " ".join(parts)


@router.get("", response_model=list[ModelSummary])
async def list_models(
    only_active: bool = True,
    session: AsyncSession = Depends(get_session),
) -> list[ModelSummary]:
    """List models. Defaults to ``is_active=true``."""
    stmt = select(ModelFit).order_by(ModelFit.ticker.asc(), ModelFit.fitted_at.desc())
    if only_active:
        stmt = stmt.where(ModelFit.is_active.is_(True))
    rows = (await session.execute(stmt)).scalars().all()
    return [
        ModelSummary(
            ticker=m.ticker,
            fitted_at=m.fitted_at,
            training_start=m.training_start,
            training_end=m.training_end,
            n_obs=m.n_obs,
            predictor_ids=list(m.predictor_ids or []),
            r2=float(m.r2),
            r2_adj=float(m.r2_adj),
            durbin_watson=float(m.durbin_watson),
            breusch_pagan_p=float(m.breusch_pagan_p),
            max_vif=float(m.max_vif),
            resid_std=float(m.resid_std) if m.resid_std is not None else None,
            estimator=m.estimator,
            alpha=float(m.alpha) if m.alpha is not None else None,
            status=m.status,
            is_active=m.is_active,
        )
        for m in rows
    ]


@router.get("/{ticker}", response_model=ModelDetail)
async def get_model(
    ticker: str,
    session: AsyncSession = Depends(get_session),
) -> ModelDetail:
    """Active model detail for one ticker."""
    stmt = (
        select(ModelFit)
        .where(ModelFit.ticker == ticker, ModelFit.is_active.is_(True))
        .limit(1)
    )
    m = (await session.execute(stmt)).scalar_one_or_none()
    if m is None:
        raise HTTPException(404, f"No active model for ticker {ticker}")

    coefs = {k: float(v) for k, v in (m.coefficients or {}).items()}
    return ModelDetail(
        ticker=m.ticker,
        fitted_at=m.fitted_at,
        training_start=m.training_start,
        training_end=m.training_end,
        n_obs=m.n_obs,
        predictor_ids=list(m.predictor_ids or []),
        r2=float(m.r2),
        r2_adj=float(m.r2_adj),
        durbin_watson=float(m.durbin_watson),
        breusch_pagan_p=float(m.breusch_pagan_p),
        max_vif=float(m.max_vif),
        resid_std=float(m.resid_std) if m.resid_std is not None else None,
        estimator=m.estimator,
        alpha=float(m.alpha) if m.alpha is not None else None,
        status=m.status,
        is_active=m.is_active,
        intercept=float(m.intercept),
        coefficients=coefs,
        equation=_equation(float(m.intercept), coefs),
    )


@router.get("/{ticker}/audit", response_model=ModelAudit)
async def audit_model(
    ticker: str,
    session: AsyncSession = Depends(get_session),
) -> ModelAudit:
    """Full-precision audit dump for the active model.

    Returns the model row with unrounded coefficients/intercept plus every
    raw observation (ticker + each predictor) inside the training window.
    Intended for human review — the auditor can re-run the OLS independently
    from this payload.
    """
    m = (
        await session.execute(
            select(ModelFit)
            .where(ModelFit.ticker == ticker, ModelFit.is_active.is_(True))
            .limit(1)
        )
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(404, f"No active model for ticker {ticker}")

    variable_ids = [ticker, *list(m.predictor_ids or [])]
    rows = (
        await session.execute(
            select(
                Observation.variable_id,
                Observation.observed_on,
                Observation.value,
                Observation.served_by_provider,
            )
            .where(
                Observation.variable_id.in_(variable_ids),
                Observation.observed_on >= m.training_start,
                Observation.observed_on <= m.training_end,
            )
            .order_by(Observation.variable_id.asc(), Observation.observed_on.asc())
        )
    ).all()

    observations = [
        ObservationAudit(
            variable_id=r[0],
            observed_on=r[1],
            value=float(r[2]),
            served_by_provider=r[3],
        )
        for r in rows
    ]

    return ModelAudit(
        model_id=str(m.id),
        ticker=m.ticker,
        fitted_at=m.fitted_at,
        training_start=m.training_start,
        training_end=m.training_end,
        n_obs=m.n_obs,
        predictor_ids=list(m.predictor_ids or []),
        intercept=float(m.intercept),
        coefficients={k: float(v) for k, v in (m.coefficients or {}).items()},
        r2=float(m.r2),
        r2_adj=float(m.r2_adj),
        durbin_watson=float(m.durbin_watson),
        breusch_pagan_p=float(m.breusch_pagan_p),
        max_vif=float(m.max_vif),
        status=m.status,
        is_active=m.is_active,
        observations=observations,
        observation_count=len(observations),
    )


@router.get("/{ticker}/validation", response_model=ValidationResult)
async def validate_model(
    ticker: str,
    train_window: int = 252,
    step: int = 5,
    estimator: str = "ols",
    cost_bps: float = 1.0,
    session: AsyncSession = Depends(get_session),
) -> ValidationResult:
    """Out-of-sample walk-forward metrics for one ticker (HER-13).

    Read-only. Recomputes on each call: rolls a ``train_window``-day window
    across the history, refitting every ``step`` days and predicting the days
    in between with the frozen model — selecting predictors *inside* each window
    so the numbers carry no look-ahead bias.
    """
    if estimator not in ESTIMATORS:
        raise HTTPException(400, f"estimator must be one of {ESTIMATORS}")
    if train_window < 30 or step < 1:
        raise HTTPException(400, "train_window must be ≥30 and step ≥1")

    res = await walk_forward(
        session,
        ticker,
        train_window=train_window,
        step=step,
        estimator=estimator,
        cost_bps=cost_bps,
    )
    return ValidationResult(
        ticker=res.ticker,
        estimator=res.estimator,
        train_window=res.train_window,
        step=res.step,
        n_windows=res.n_windows,
        n_predictions=res.n_predictions,
        hit_rate=res.hit_rate,
        up_day_base_rate=res.up_day_base_rate,
        edge_vs_base=res.edge_vs_base,
        hit_rate_pvalue=res.hit_rate_pvalue,
        significant=res.significant,
        rmse=res.rmse,
        mae=res.mae,
        sharpe_strategy=res.sharpe_strategy,
        sharpe_buy_hold=res.sharpe_buy_hold,
        total_return_strategy=res.total_return_strategy,
        total_return_buy_hold=res.total_return_buy_hold,
        cost_bps=res.cost_bps,
        curve=[
            ValidationCurvePoint(on=c.on, strategy=c.strategy, buy_hold=c.buy_hold)
            for c in res.curve
        ],
        note=res.note,
    )


@router.get("/{ticker}/forecast", response_model=ForecastResult)
async def forecast_model(
    ticker: str,
    horizon: int = 5,
    session: AsyncSession = Depends(get_session),
) -> ForecastResult:
    """Price forecast with a widening confidence band (HER-17).

    Day 1 uses the model's one-step prediction from the latest predictors;
    days 2..N drift at the baseline (we don't know the predictors' future
    values). The band widens as σ·√t. Read-only.
    """
    if horizon < 1 or horizon > 60:
        raise HTTPException(400, "horizon must be between 1 and 60")

    m = await load_active_model(session, ticker)
    if m is None:
        raise HTTPException(404, f"No active model for ticker {ticker}")

    last = await latest_price(session, ticker)
    if last is None:
        raise HTTPException(404, f"No price observations for ticker {ticker}")
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
    day1_return = intercept  # baseline drift if we can't read the predictors

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

    # Confidence-band sigma: the model's residual std if recorded (HER-16),
    # otherwise the ticker's recent realized daily volatility.
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

    return ForecastResult(
        ticker=ticker,
        as_of=as_of,
        last_price=last_price_value,
        horizon=horizon,
        confidence=0.90,
        direction="up" if day1_return >= 0 else "down",
        direction_symbol="▲" if day1_return >= 0 else "▼",
        expected_return_1d=day1_return,
        sigma_daily=sigma_daily,
        status=m.status,
        estimator=m.estimator,
        sigma_source=sigma_source,
        points=[
            ForecastPoint(on=p.on, day=p.day, central=p.central, lower=p.lower, upper=p.upper)
            for p in points
        ],
        note=note,
    )


@router.post(
    "/refit_all",
    response_model=list[RefitOutcomeOut],
    dependencies=[Depends(require_admin)],
)
async def refit_all_models(req: RefitRequest | None = None) -> list[RefitOutcomeOut]:
    """Re-fit every stock; runs synchronously and returns per-ticker outcomes."""
    req = req or RefitRequest()
    async with AsyncSessionLocal() as session:
        outcomes = await refit_all(
            session,
            lookback_days=req.lookback_days,
            k_per_stock=req.k_per_stock,
            lag_days=req.lag_days,
            estimator=req.estimator,
            alpha=req.alpha,
            allow_reuse=req.allow_reuse,
        )
    return [
        RefitOutcomeOut(
            ticker=o.ticker,
            status=o.status,
            r2=o.r2,
            n_obs=o.n_obs,
            predictor_ids=o.predictor_ids,
            error=o.error,
        )
        for o in outcomes
    ]


@router.post(
    "/{ticker}/refit",
    response_model=RefitOutcomeOut,
    dependencies=[Depends(require_admin)],
)
async def refit_one_model(ticker: str) -> RefitOutcomeOut:
    """Re-fit a single ticker. Returns its outcome record.

    Only the specified ticker is processed; other tickers' active models are
    left untouched.
    """
    async with AsyncSessionLocal() as session:
        outcomes = await refit_all(session, only_ticker=ticker)
    if not outcomes:
        raise HTTPException(404, f"Ticker {ticker} not in active stock set")
    o = outcomes[0]
    return RefitOutcomeOut(
        ticker=o.ticker,
        status=o.status,
        r2=o.r2,
        n_obs=o.n_obs,
        predictor_ids=o.predictor_ids,
        error=o.error,
    )
