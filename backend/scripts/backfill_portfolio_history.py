"""Backfill realistic historic portfolio_snapshots.

The first cron-run snapshot only happens once tomorrow's daily job runs, so
the weight-evolution chart on /portfolios/[id] is empty until then. To give
the chart immediate signal, this script walks back ~30 business days and
computes what each profile's weights WOULD have been on each day, using
that day's rolling 90-day vol + Sharpe via the same build_portfolios()
function the cron uses.

Honesty notes:
  - The R² inputs are the CURRENT model R²s (we don't have historical fits)
    so the "confidence" component of P1/P2 is constant across time. The
    variance you see comes entirely from rolling-window vol + Sharpe rank
    changes, which is real data.
  - This is a one-shot backfill. Real cron snapshots accumulate on top.

Usage:
    uv run python scripts/backfill_portfolio_history.py [--days 30]
"""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, date, datetime, time, timedelta

import pandas as pd
import structlog
import typer
from sqlalchemy import delete, select

from app.db import AsyncSessionLocal
from app.logging import setup_logging
from app.modeling.data import load_returns_frame
from app.models import ModelFit, PortfolioSnapshot, Variable
from app.portfolio.optimizer import build_portfolios

setup_logging()
log = structlog.get_logger(__name__)

app = typer.Typer(pretty_exceptions_enable=False)


async def _backfill(days: int, wipe: bool) -> None:
    end = date.today()
    # Need extra history to compute rolling 90d metrics; days back + 120 cushion
    fetch_start = end - timedelta(days=days + 120)

    async with AsyncSessionLocal() as s:
        if wipe:
            n_deleted = (await s.execute(delete(PortfolioSnapshot))).rowcount
            await s.commit()
            log.info("wiped_existing_snapshots", n=n_deleted)

        tickers = [
            r[0]
            for r in (
                await s.execute(
                    select(Variable.id).where(Variable.kind == "stock")
                )
            ).all()
        ]

        # Use current R²s as the confidence input. Only PASS models have
        # is_active=True; for REVIEW/skipped tickers we fall back to a low
        # R² (0.02 = the new acceptance floor) so they get included with
        # minimal confidence.
        active_models = (
            await s.execute(
                select(ModelFit.ticker, ModelFit.r2).where(ModelFit.is_active.is_(True))
            )
        ).all()
        r2_by_ticker: dict[str, float] = {t: float(r) for t, r in active_models}
        for t in tickers:
            r2_by_ticker.setdefault(t, 0.02)

        returns = await load_returns_frame(
            s, variable_ids=tickers, start=fetch_start, end=end
        )

        if returns.empty:
            log.warning("backfill_no_returns")
            return

        # Determine the last N business days to snapshot
        bday_index = pd.bdate_range(end - timedelta(days=days), end)
        n_inserted = 0
        for snap_ts in bday_index:
            snap_date = snap_ts.date()
            window_start = snap_ts - pd.Timedelta(days=90)
            window = returns.loc[window_start:snap_ts]

            metrics_rows = []
            for t in tickers:
                if t not in window.columns:
                    continue
                series = window[t].dropna()
                if len(series) < 30:
                    continue
                vol_annual = float(series.std() * math.sqrt(252))
                # Rough 30-day-ahead return proxy: mean daily × 30
                pred_ret_30d = float(series.mean() * 30.0)
                sharpe = (
                    pred_ret_30d * (252.0 / 30.0) / vol_annual
                    if vol_annual > 0
                    else 0.0
                )
                metrics_rows.append(
                    {
                        "ticker": t,
                        "r2": r2_by_ticker[t],
                        "pred_ret_30d": pred_ret_30d,
                        "vol_annual": vol_annual,
                        "sharpe": sharpe,
                    }
                )

            if not metrics_rows:
                continue

            metrics_df = pd.DataFrame(metrics_rows).set_index("ticker")
            try:
                profiles = build_portfolios(metrics_df)
            except ValueError as e:
                log.warning("build_failed", date=snap_date, err=str(e))
                continue

            # Use 22:00 UTC on the snap_date as the timestamp — matches what
            # the real cron emits each evening.
            ts = datetime.combine(snap_date, time(22, 0), tzinfo=UTC)

            for pid, weights in profiles.items():
                s.add(
                    PortfolioSnapshot(
                        portfolio_id=pid,
                        weights=weights,
                        snapshotted_at=ts,
                    )
                )
                n_inserted += 1

        await s.commit()
        log.info("backfill_done", inserted=n_inserted, days=days)
        print(f"Inserted {n_inserted} snapshots across {days} days.")


@app.command()
def main(
    days: int = typer.Option(30, help="How many business days back to backfill"),
    wipe: bool = typer.Option(True, help="Delete existing snapshots first"),
) -> None:
    asyncio.run(_backfill(days, wipe))


if __name__ == "__main__":
    app()
