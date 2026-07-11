"""Admin endpoints — protected by ADMIN_BEARER_TOKEN.

POST /admin/run-trading      →  runs all 3 trading bots (triggered by cron-job.org)
POST /admin/run-predictions  →  runs daily predictions pipeline (backfill + predict + rebuild)
POST /admin/run-ingestion    →  fetches latest price observations for all seeded variables
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import select

from app.auth import require_admin

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def _run_script(script: str) -> dict:
    """Run a backend script in a subprocess and return stdout/stderr."""
    result = subprocess.run(
        [sys.executable, "-m", script] if False else ["python", script],
        capture_output=True,
        text=True,
        timeout=600,
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout[-3000:] if result.stdout else "",
        "stderr": result.stderr[-2000:] if result.stderr else "",
    }


async def _run_trading_task(session):
    from app.trading.runner import run_all
    results = await run_all(session, dry_run=False)
    for r in results:
        if r.success:
            log.info("[admin] %s — %d actions", r.name, len(r.actions))
        else:
            log.error("[admin] %s FAILED: %s", r.name, r.error)
    return results


@router.post("/run-trading")
async def run_trading(
    background_tasks: BackgroundTasks,
    _: None = Depends(require_admin),
):
    """Trigger all 3 trading bots. Called by cron-job.org every weekday at 9:25 AM ET."""
    started = datetime.now(UTC).isoformat()
    log.info("[admin] run-trading triggered at %s", started)

    async def _task():
        from app.db import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            try:
                await _run_trading_task(session)
            except Exception as e:
                log.error("[admin] run-trading background task failed: %s", e)

    background_tasks.add_task(_task)
    return {"status": "accepted", "started_at": started, "message": "Trading bots running in background"}


@router.post("/run-predictions")
async def run_predictions(
    background_tasks: BackgroundTasks,
    _: None = Depends(require_admin),
):
    """Trigger daily predictions pipeline. Called before run-trading."""
    started = datetime.now(UTC).isoformat()
    log.info("[admin] run-predictions triggered at %s", started)

    async def _task():
        try:
            proc = await asyncio.create_subprocess_exec(
                "python", "scripts/run_predictions.py",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=540)
            log.info("[admin] run-predictions done (rc=%s): %s", proc.returncode, (stdout or b"").decode()[-2000:])
        except Exception as e:
            log.error("[admin] run-predictions failed: %s", e)

    background_tasks.add_task(_task)
    return {"status": "accepted", "started_at": started, "message": "Predictions pipeline running in background"}


@router.post("/run-ingestion")
async def run_ingestion(
    background_tasks: BackgroundTasks,
    days: int = 90,
    _: None = Depends(require_admin),
):
    """Fetch latest price observations for all seeded variables.

    Must run BEFORE run-predictions so backfill_actuals can find actual prices.
    Called by cron-job.org every weekday at 9:00 AM ET (before run-predictions).
    """
    started = datetime.now(UTC).isoformat()
    log.info("[admin] run-ingestion triggered at %s, days=%d", started, days)

    async def _task():
        try:
            proc = await asyncio.create_subprocess_exec(
                "python", "scripts/run_ingestion.py",
                "--days", str(days),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=540)
            log.info("[admin] run-ingestion done (rc=%s): %s", proc.returncode, (stdout or b"").decode()[-2000:])
        except Exception as e:
            log.error("[admin] run-ingestion failed: %s", e)

    background_tasks.add_task(_task)
    return {"status": "accepted", "started_at": started, "message": f"Ingestion running in background (days={days})"}


@router.get("/trading-runs")
async def get_trading_runs(
    limit: int = 30,
    _: None = Depends(require_admin),
):
    """Return recent trading run logs."""
    from app.db import AsyncSessionLocal
    from app.models.trading_run import TradingRun
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(TradingRun).order_by(TradingRun.ran_at.desc()).limit(limit)
        )).scalars().all()
        return [
            {
                "id":     str(r.id),
                "ran_at": r.ran_at.isoformat(),
                "bot":    r.bot,
                "status": r.status,
                "trades": r.trades,
                "error":  r.error,
            }
            for r in rows
        ]
