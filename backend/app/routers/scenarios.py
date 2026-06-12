"""Scenario endpoints — named scenario portfolios (Phase 1: read-only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import ttl_cache
from app.db import get_session
from app.models import Scenario
from app.schemas import ScenarioOut

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


@router.get("", response_model=list[ScenarioOut])
@ttl_cache(seconds=3600)
async def list_scenarios(
    response: Response,
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[ScenarioOut]:
    """List scenarios, ordered for the tab bar. Optional ?status=public|draft."""
    stmt = select(Scenario).order_by(
        Scenario.display_order.asc(), Scenario.created_at.asc()
    )
    if status:
        stmt = stmt.where(Scenario.status == status)
    rows = (await session.execute(stmt)).scalars().all()
    return [ScenarioOut.model_validate(s) for s in rows]


@router.get("/{slug}", response_model=ScenarioOut)
@ttl_cache(seconds=3600)
async def get_scenario(
    slug: str,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> ScenarioOut:
    """Single scenario by slug."""
    s = (
        await session.execute(select(Scenario).where(Scenario.slug == slug))
    ).scalar_one_or_none()
    if s is None:
        raise HTTPException(404, f"Scenario {slug!r} not found")
    return ScenarioOut.model_validate(s)
