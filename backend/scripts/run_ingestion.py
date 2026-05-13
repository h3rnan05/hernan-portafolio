"""Manual ingestion trigger.

Usage:
    uv run python scripts/run_ingestion.py                       # all configured providers
    uv run python scripts/run_ingestion.py --providers fred      # only FRED
    uv run python scripts/run_ingestion.py --days 365            # 1-year backfill

This is the same code path the scheduled cron will exercise — so a successful
manual run here means the cron will work too.
"""

from __future__ import annotations

import asyncio

import structlog
import typer

from app.db import AsyncSessionLocal
from app.ingestion import (
    BalticDryIndexProvider,
    FREDProvider,
    IngestionRunner,
    ISMManufacturingPMIProvider,
    PolygonProvider,
    StooqProvider,
    TwelveDataProvider,
    YFinanceProvider,
)
from app.logging import setup_logging

setup_logging()
log = structlog.get_logger(__name__)

app = typer.Typer(pretty_exceptions_enable=False)


_PROVIDER_FACTORIES = {
    "fred": FREDProvider,
    "twelve_data": TwelveDataProvider,
    "polygon": PolygonProvider,
    "yfinance": YFinanceProvider,
    "stooq": StooqProvider,  # legacy — kept for re-enable if Stooq opens up again
    "scrape_baltic": BalticDryIndexProvider,
    "scrape_ism": ISMManufacturingPMIProvider,
}


def build_runner(enabled: list[str]) -> IngestionRunner:
    """Construct an IngestionRunner with only the requested providers loaded.

    Add new providers by extending ``_PROVIDER_FACTORIES`` above and
    referencing the same key from ``Variable.providers[*].name`` in the seed.
    """
    providers: dict[str, object] = {}
    unknown: list[str] = []
    for name in enabled:
        factory = _PROVIDER_FACTORIES.get(name)
        if factory is None:
            unknown.append(name)
            continue
        try:
            providers[name] = factory()
        except Exception as e:
            log.error("provider_init_failed", provider=name, err=str(e))
            raise
    if unknown:
        log.warning("unknown_providers_requested", providers=unknown)
    if not providers:
        raise ValueError(f"No known providers in {enabled}")
    return IngestionRunner(providers=providers)  # type: ignore[arg-type]


@app.command()
def run(
    providers: str = typer.Option(
        "fred,twelve_data,polygon,yfinance,scrape_baltic,scrape_ism",
        "--providers",
        help=(
            "Comma-separated provider names. Known: "
            "fred, twelve_data, polygon, yfinance, stooq, scrape_baltic, scrape_ism"
        ),
    ),
    days: int = typer.Option(90, help="How many days back to fetch"),
) -> None:
    """Run a one-shot ingestion."""
    enabled = [p.strip() for p in providers.split(",") if p.strip()]
    runner = build_runner(enabled)

    async def _run() -> None:
        async with AsyncSessionLocal() as session:
            results = await runner.run_all(
                session,
                provider_filter=enabled,
                days_back=days,
            )
        total = sum(results.values())
        print(f"Ingested {total} rows total")
        for prov, n in results.items():
            print(f"  {prov:12s} {n:6d}")

    asyncio.run(_run())


if __name__ == "__main__":
    app()
