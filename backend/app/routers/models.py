"""Model-fit endpoints: list, detail, refit (admin)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.db import AsyncSessionLocal, get_session
from app.modeling.refit import refit_all
from app.models import ModelFit, Observation
from app.schemas import (
    ModelAudit,
    ModelDetail,
    ModelSummary,
    ObservationAudit,
    RefitOutcomeOut,
    RefitRequest,
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
