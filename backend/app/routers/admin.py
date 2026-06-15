"""Admin endpoints — protected by ADMIN_BEARER_TOKEN.

POST /admin/run-trading  →  runs all 3 trading bots (triggered by cron-job.org)
POST /admin/run-predictions  →  runs daily predictions pipeline
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.auth import require_admin
from app.db import get_session

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
    started = datetime.now(timezone.utc).isoformat()
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
    started = datetime.now(timezone.utc).isoformat()
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
