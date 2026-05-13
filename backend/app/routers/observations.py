"""Observations endpoint — time series for a variable."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Observation, Variable
from app.schemas import ObservationOut

router = APIRouter(prefix="/observations", tags=["observations"])


@router.get("/{variable_id}", response_model=list[ObservationOut])
async def get_observations(
    variable_id: str,
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    limit: int = Query(1000, ge=1, le=10000),
    session: AsyncSession = Depends(get_session),
) -> list[ObservationOut]:
    """Time-series for one variable, oldest first."""
    var = await session.get(Variable, variable_id)
    if var is None:
        raise HTTPException(status_code=404, detail=f"Variable {variable_id} not found")

    stmt = select(Observation).where(Observation.variable_id == variable_id)
    if from_date:
        stmt = stmt.where(Observation.observed_on >= from_date)
    if to_date:
        stmt = stmt.where(Observation.observed_on <= to_date)
    stmt = stmt.order_by(Observation.observed_on.asc()).limit(limit)

    rows = (await session.execute(stmt)).scalars().all()
    return [
        ObservationOut(
            observed_on=r.observed_on,
            value=float(r.value),
            served_by_provider=r.served_by_provider,
        )
        for r in rows
    ]
