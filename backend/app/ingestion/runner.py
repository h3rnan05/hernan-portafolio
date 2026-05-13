"""IngestionRunner — orchestrates providers via the fallback chain.

For each Variable, walks `variables.providers` in order and returns on first
success. Writes results to `observations` with ON CONFLICT DO UPDATE
(idempotent re-runs).
"""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, date, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.base import (
    DataPoint,
    Provider,
    ProviderError,
    ProviderRateLimited,
    ProviderTimeout,
)
from app.models import IngestionRun, Observation, Variable

log = structlog.get_logger(__name__)


class IngestionRunner:
    """Pulls every active Variable through its provider chain into the DB."""

    def __init__(self, providers: dict[str, Provider]) -> None:
        """`providers` maps provider name → instance, e.g. {"fred": FREDProvider(), ...}."""
        self.providers = providers

    async def run_all(
        self,
        session: AsyncSession,
        provider_filter: list[str] | None = None,
        days_back: int = 90,
    ) -> dict[str, int]:
        """Ingest every active Variable. Returns {provider_name: rows_inserted}.

        `provider_filter` — if set, only ingest variables whose *primary*
        provider is in this list (useful for "run only FRED today").
        `days_back` — how far back to fetch on each call (idempotent).
        """
        end = date.today()
        start = end - timedelta(days=days_back)

        # Load all active variables that need ingesting (skip 'portfolio' kind)
        result = await session.execute(
            select(Variable).where(
                Variable.active.is_(True),
                Variable.kind.in_(("predictor", "stock")),
            )
        )
        variables = list(result.scalars().all())
        log.info("ingestion_start", n_variables=len(variables), start=start, end=end)

        # One IngestionRun row per provider in this batch
        runs_by_provider: dict[str, IngestionRun] = {}

        rows_per_provider: dict[str, int] = {p: 0 for p in self.providers}

        for var in variables:
            chain = var.providers or []
            if provider_filter:
                chain = [p for p in chain if p["name"] in provider_filter]
                if not chain:
                    continue

            point_count, served_by = await self._fetch_with_fallback(
                var.id, chain, start, end
            )

            if point_count == 0:
                log.warning("variable_no_data", variable_id=var.id, chain=chain)
                continue

            # Get-or-create the run row for this provider
            if served_by not in runs_by_provider:
                run = IngestionRun(provider=served_by, status="running")
                session.add(run)
                await session.flush()
                runs_by_provider[served_by] = run

            run = runs_by_provider[served_by]
            run.rows_inserted = (run.rows_inserted or 0) + point_count
            rows_per_provider[served_by] = rows_per_provider.get(served_by, 0) + point_count

        # Mark all runs complete
        for run in runs_by_provider.values():
            run.status = "ok"
            run.completed_at = datetime.now(UTC)

        await session.commit()
        log.info("ingestion_done", rows_per_provider=rows_per_provider)
        return rows_per_provider

    async def _fetch_with_fallback(
        self,
        variable_id: str,
        chain: list[dict],
        start: date,
        end: date,
    ) -> tuple[int, str]:
        """Try each provider in `chain` until one succeeds. Returns (rows_written, provider_name).

        On total failure, returns (0, "none").
        """
        for cfg in chain:
            provider_name = cfg["name"]
            symbol = cfg["symbol"]
            provider = self.providers.get(provider_name)
            if provider is None:
                log.debug(
                    "provider_not_loaded",
                    variable_id=variable_id,
                    provider=provider_name,
                )
                continue

            try:
                points = await provider.fetch(symbol, start, end)
            except ProviderRateLimited:
                log.warning(
                    "provider_rate_limited",
                    variable_id=variable_id,
                    provider=provider_name,
                )
                # Light jitter before trying next provider — avoids hammering
                await asyncio.sleep(1 + random.random())
                continue
            except ProviderTimeout:
                log.warning(
                    "provider_timeout",
                    variable_id=variable_id,
                    provider=provider_name,
                )
                continue
            except ProviderError as e:
                log.warning(
                    "provider_error",
                    variable_id=variable_id,
                    provider=provider_name,
                    err=str(e),
                )
                continue

            if not points:
                continue

            written = await self._upsert_points(variable_id, points, provider_name)
            log.info(
                "variable_ingested",
                variable_id=variable_id,
                provider=provider_name,
                rows=written,
            )
            return (written, provider_name)

        return (0, "none")

    async def _upsert_points(
        self,
        variable_id: str,
        points: list[DataPoint],
        provider_name: str,
    ) -> int:
        """Bulk upsert observations with ON CONFLICT (variable_id, observed_on) DO UPDATE."""
        from app.db import AsyncSessionLocal

        if not points:
            return 0

        rows = [
            {
                "variable_id": variable_id,
                "observed_on": p.observed_on,
                "value": p.value,
                "served_by_provider": provider_name,
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
        async with AsyncSessionLocal() as s:
            await s.execute(stmt)
            await s.commit()
        return len(rows)
