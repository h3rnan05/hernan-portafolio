"""CLI: refit every active model.

Usage:
    uv run python scripts/refit_all.py
    uv run python scripts/refit_all.py --lookback 365 --k 4
"""

from __future__ import annotations

import asyncio

import structlog
import typer

from app.config import K_PER_STOCK, get_settings
from app.db import AsyncSessionLocal
from app.logging import setup_logging
from app.modeling.refit import refit_all

setup_logging()
log = structlog.get_logger(__name__)

app = typer.Typer(pretty_exceptions_enable=False)


@app.command()
def main(
    lookback: int = typer.Option(540, help="Days of training data to use"),
    k: int = typer.Option(K_PER_STOCK, "--k", help="Predictors per stock"),
    lag: int = typer.Option(0, help="Override LAG_DAYS (0 = use config)"),
    min_obs: int = typer.Option(60, help="Minimum aligned rows before fitting"),
) -> None:
    settings = get_settings()
    lag_days = lag or settings.lag_days

    async def _run() -> None:
        async with AsyncSessionLocal() as session:
            results = await refit_all(
                session,
                lookback_days=lookback,
                k_per_stock=k,
                lag_days=lag_days,
                min_obs=min_obs,
            )

        passed = sum(1 for r in results if r.status == "PASS")
        review = sum(1 for r in results if r.status == "REVIEW")
        skipped = sum(1 for r in results if r.status == "SKIPPED")

        print(f"\nRefit complete — {passed} PASS · {review} REVIEW · {skipped} SKIPPED\n")
        print(f"  {'TICKER':<8} {'STATUS':<8} {'R²':>7} {'N':>5}  PREDICTORS")
        for r in results:
            r2 = f"{r.r2:.3f}" if r.r2 is not None else "—"
            n = str(r.n_obs) if r.n_obs is not None else "—"
            preds = ", ".join(r.predictor_ids) if r.predictor_ids else r.error or "—"
            print(f"  {r.ticker:<8} {r.status:<8} {r2:>7} {n:>5}  {preds}")

    asyncio.run(_run())


if __name__ == "__main__":
    app()
