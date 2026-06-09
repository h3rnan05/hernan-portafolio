"""One-off data repair: fix the corrupted FTSE_100 history.

Root cause
----------
FTSE_100 is declared in GBX (pence) and primary-sourced from the iShares Core
FTSE 100 ETF (`ISF.LSE` on EODHD, ≈ 1,000 pence). Two fallback providers were
misconfigured to return the FTSE 100 *index in points* (≈ 10,000):
    twelve_data UKX  /  yfinance ^FTSE
So whenever EODHD fell back, the series jumped ~10x. On the Overview comparison
chart (everything re-based to 100) that made FTSE crater ~90%.

This script — scoped to ONLY the FTSE_100 variable — :
    1. rewrites the variable's provider chain to ETF-only, on-scale sources,
    2. deletes the wrong-scale (index, value > THRESHOLD) observations,
    3. re-fetches a clean ISF.LSE (pence) history from EODHD and upserts it.

Idempotent — safe to re-run. The seed (`seed_variables.py`) carries the same
corrected chain so a future re-seed stays consistent.

Run:
    uv run python scripts/fix_ftse_history.py
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import structlog
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import AsyncSessionLocal
from app.ingestion import EODHDProvider
from app.logging import setup_logging
from app.models import Observation, Variable

setup_logging()
log = structlog.get_logger(__name__)

VAR_ID = "FTSE_100"
EODHD_SYMBOL = "ISF.LSE"
# Same ETF, pence scale, on yfinance — keeps the fallback on-scale.
CORRECTED_CHAIN = [
    {"name": "eodhd", "symbol": "ISF.LSE"},
    {"name": "yfinance", "symbol": "ISF.L"},
]
# ISF ETF trades ≈ 1,000 pence; the FTSE 100 index ≈ 10,000 points. Anything
# above this is unmistakably the wrong (index-scale) instrument.
WRONG_SCALE_THRESHOLD = 3000.0
DAYS_BACK = 420  # cover the 360-day chart window with margin


async def main() -> None:
    end = date.today()
    start = end - timedelta(days=DAYS_BACK)

    # 1) Pull a clean, single-scale ETF history straight from EODHD.
    provider = EODHDProvider()
    points = await provider.fetch(EODHD_SYMBOL, start, end)
    log.info("eodhd_fetched", symbol=EODHD_SYMBOL, points=len(points))
    if not points:
        raise SystemExit(
            "EODHD returned no ISF.LSE points — aborting before mutating data."
        )
    sample = sorted(p.value for p in points)
    log.info("eodhd_value_range", min=round(sample[0], 2), max=round(sample[-1], 2))
    if sample[-1] > WRONG_SCALE_THRESHOLD:
        raise SystemExit(
            f"EODHD ISF.LSE max {sample[-1]} looks index-scale, not pence — "
            "aborting so we don't write bad data."
        )

    async with AsyncSessionLocal() as s:
        # 2) Correct the provider chain on the variable.
        await s.execute(
            update(Variable)
            .where(Variable.id == VAR_ID)
            .values(providers=CORRECTED_CHAIN)
        )

        # 3) Drop the wrong-scale (index) observations.
        before = (
            await s.execute(
                select(Observation.observed_on).where(
                    Observation.variable_id == VAR_ID,
                    Observation.value > WRONG_SCALE_THRESHOLD,
                )
            )
        ).all()
        await s.execute(
            delete(Observation).where(
                Observation.variable_id == VAR_ID,
                Observation.value > WRONG_SCALE_THRESHOLD,
            )
        )
        log.info("deleted_wrong_scale_rows", count=len(before))

        # 4) Upsert the clean ETF history (idempotent on (variable_id, date)).
        rows = [
            {
                "variable_id": VAR_ID,
                "observed_on": p.observed_on,
                "value": p.value,
                "served_by_provider": "eodhd",
            }
            for p in points
        ]
        stmt = pg_insert(Observation).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["variable_id", "observed_on"],
            set_={
                "value": stmt.excluded.value,
                "served_by_provider": stmt.excluded.served_by_provider,
            },
        )
        await s.execute(stmt)
        await s.commit()
        log.info("upserted_clean_rows", count=len(rows))

        # 5) Verify nothing wrong-scale survives.
        leftover = (
            await s.execute(
                select(Observation.observed_on).where(
                    Observation.variable_id == VAR_ID,
                    Observation.value > WRONG_SCALE_THRESHOLD,
                )
            )
        ).all()
        if leftover:
            raise SystemExit(
                f"{len(leftover)} wrong-scale rows still present after repair."
            )

    print(
        f"FTSE_100 repaired: deleted {len(before)} index-scale rows, "
        f"upserted {len(rows)} ETF (pence) rows, chain set to ETF-only."
    )


if __name__ == "__main__":
    asyncio.run(main())
