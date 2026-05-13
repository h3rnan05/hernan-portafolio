"""CLI: run the daily prediction + actuals backfill + portfolio rebuild.

Usage:
    uv run python scripts/run_predictions.py
    uv run python scripts/run_predictions.py --skip-backfill
"""

from __future__ import annotations

import asyncio

import structlog
import typer

from app.db import AsyncSessionLocal
from app.logging import setup_logging
from app.portfolio.runner import (
    backfill_actuals,
    rebuild_portfolios,
    run_daily_predictions,
)

setup_logging()
log = structlog.get_logger(__name__)

app = typer.Typer(pretty_exceptions_enable=False)


@app.command()
def main(
    skip_backfill: bool = typer.Option(False, "--skip-backfill"),
    skip_portfolios: bool = typer.Option(False, "--skip-portfolios"),
    skip_predictions: bool = typer.Option(False, "--skip-predictions"),
) -> None:
    async def _run() -> None:
        async with AsyncSessionLocal() as session:
            n_back = 0
            if not skip_backfill:
                n_back = await backfill_actuals(session)
            outcomes = []
            if not skip_predictions:
                outcomes = await run_daily_predictions(session)
            profiles = {}
            if not skip_portfolios:
                profiles = await rebuild_portfolios(session)

        print(f"\nBackfilled actuals: {n_back}")
        print(f"Predictions made:   {len(outcomes)}")
        for o in outcomes:
            print(
                f"  {o.ticker:<8} ret={o.predicted_return:+.5f} "
                f"price={o.predicted_price:.4f}  (last={o.last_price:.4f})"
            )
        print(f"Portfolios rebuilt: {len(profiles)}")

    asyncio.run(_run())


if __name__ == "__main__":
    app()
