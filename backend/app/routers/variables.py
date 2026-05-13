"""Variables endpoint — list all tracked variables."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Observation, Variable
from app.schemas import VariableOut

router = APIRouter(prefix="/variables", tags=["variables"])


@router.get("", response_model=list[VariableOut])
async def list_variables(
    kind: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[VariableOut]:
    """List all variables, with their last-observed date and value."""
    # Subquery: most recent observation per variable
    last_obs_sq = (
        select(
            Observation.variable_id,
            func.max(Observation.observed_on).label("last_date"),
        )
        .group_by(Observation.variable_id)
        .subquery()
    )

    stmt = (
        select(
            Variable,
            last_obs_sq.c.last_date,
            Observation.value,
        )
        .outerjoin(last_obs_sq, last_obs_sq.c.variable_id == Variable.id)
        .outerjoin(
            Observation,
            (Observation.variable_id == Variable.id)
            & (Observation.observed_on == last_obs_sq.c.last_date),
        )
        .where(Variable.active.is_(True))
    )
    if kind:
        stmt = stmt.where(Variable.kind == kind)
    stmt = stmt.order_by(Variable.kind, Variable.id)

    result = await session.execute(stmt)
    out: list[VariableOut] = []
    for var, last_date, last_value in result.all():
        out.append(
            VariableOut(
                id=var.id,
                display_name=var.display_name,
                kind=var.kind,
                category=var.category,
                unit=var.unit,
                providers=var.providers,
                active=var.active,
                last_observed_on=last_date,
                last_value=float(last_value) if last_value is not None else None,
            )
        )
    return out


@router.get("/{variable_id}", response_model=VariableOut)
async def get_variable(
    variable_id: str,
    session: AsyncSession = Depends(get_session),
) -> VariableOut:
    """Single variable detail."""
    var = await session.get(Variable, variable_id)
    if var is None:
        raise HTTPException(status_code=404, detail=f"Variable {variable_id} not found")

    last_obs_stmt = (
        select(Observation)
        .where(Observation.variable_id == variable_id)
        .order_by(Observation.observed_on.desc())
        .limit(1)
    )
    last_obs = (await session.execute(last_obs_stmt)).scalar_one_or_none()

    return VariableOut(
        id=var.id,
        display_name=var.display_name,
        kind=var.kind,
        category=var.category,
        unit=var.unit,
        providers=var.providers,
        active=var.active,
        last_observed_on=last_obs.observed_on if last_obs else None,
        last_value=float(last_obs.value) if last_obs else None,
    )
