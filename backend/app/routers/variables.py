"""Variables endpoint — list all tracked variables."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.db import get_session
from app.models import Observation, Variable
from app.schemas import VariableCreate, VariableOut, VariablePatch

router = APIRouter(prefix="/variables", tags=["variables"])


def _build_out(var: Variable, last_date=None, last_value=None) -> VariableOut:
    return VariableOut(
        id=var.id,
        display_name=var.display_name,
        kind=var.kind,
        category=var.category,
        unit=var.unit,
        providers=var.providers,
        active=var.active,
        is_target=var.is_target,
        lag_days=var.lag_days,
        transform=var.transform,
        last_observed_on=last_date,
        last_value=float(last_value) if last_value is not None else None,
    )


@router.get("", response_model=list[VariableOut])
async def list_variables(
    kind: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[VariableOut]:
    """List all variables, with their last-observed date and value."""
    last_obs_sq = (
        select(
            Observation.variable_id,
            func.max(Observation.observed_on).label("last_date"),
        )
        .group_by(Observation.variable_id)
        .subquery()
    )

    stmt = (
        select(Variable, last_obs_sq.c.last_date, Observation.value)
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
    return [_build_out(var, last_date, last_value) for var, last_date, last_value in result.all()]


@router.get("/{variable_id}", response_model=VariableOut)
async def get_variable(
    variable_id: str,
    session: AsyncSession = Depends(get_session),
) -> VariableOut:
    """Single variable detail."""
    var = await session.get(Variable, variable_id)
    if var is None:
        raise HTTPException(status_code=404, detail=f"Variable {variable_id} not found")

    last_obs = (
        await session.execute(
            select(Observation)
            .where(Observation.variable_id == variable_id)
            .order_by(Observation.observed_on.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    return _build_out(
        var,
        last_obs.observed_on if last_obs else None,
        last_obs.value if last_obs else None,
    )


@router.post(
    "",
    response_model=VariableOut,
    status_code=201,
    dependencies=[Depends(require_admin)],
)
async def create_variable(
    body: VariableCreate,
    session: AsyncSession = Depends(get_session),
) -> VariableOut:
    """Register a new variable. Fails if the ID already exists."""
    existing = await session.get(Variable, body.id)
    if existing is not None:
        raise HTTPException(409, f"Variable {body.id!r} already exists")

    var = Variable(
        id=body.id,
        display_name=body.display_name,
        kind=body.kind,
        category=body.category,
        unit=body.unit,
        providers=[p.model_dump() for p in body.providers],
        active=True,
        is_target=body.is_target,
        lag_days=body.lag_days,
        transform=body.transform,
    )
    session.add(var)
    await session.commit()
    await session.refresh(var)
    return _build_out(var)


@router.patch(
    "/{variable_id}",
    response_model=VariableOut,
    dependencies=[Depends(require_admin)],
)
async def patch_variable(
    variable_id: str,
    body: VariablePatch,
    session: AsyncSession = Depends(get_session),
) -> VariableOut:
    """Update mutable fields on a variable (active, is_target, display_name, etc.)."""
    var = await session.get(Variable, variable_id)
    if var is None:
        raise HTTPException(404, f"Variable {variable_id} not found")

    if body.display_name is not None:
        var.display_name = body.display_name
    if body.category is not None:
        var.category = body.category
    if body.unit is not None:
        var.unit = body.unit
    if body.providers is not None:
        var.providers = [p.model_dump() for p in body.providers]
    if body.active is not None:
        var.active = body.active
    if body.is_target is not None:
        var.is_target = body.is_target
    if body.lag_days is not None:
        var.lag_days = body.lag_days
    if body.transform is not None:
        var.transform = body.transform

    await session.commit()
    await session.refresh(var)
    return _build_out(var)


@router.delete(
    "/{variable_id}",
    status_code=204,
    dependencies=[Depends(require_admin)],
)
async def delete_variable(
    variable_id: str,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Soft-delete: sets active=False and is_target=False. Does not remove rows."""
    var = await session.get(Variable, variable_id)
    if var is None:
        raise HTTPException(404, f"Variable {variable_id} not found")
    var.active = False
    var.is_target = False
    await session.commit()
