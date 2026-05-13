"""Health endpoint — liveness + DB connectivity check."""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.db import get_session

router = APIRouter(tags=["meta"])


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict:
    """Liveness check. Returns 200 if app + DB are reachable."""
    try:
        await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    return {
        "ok": db_ok,
        "version": __version__,
        "db_reachable": db_ok,
    }
