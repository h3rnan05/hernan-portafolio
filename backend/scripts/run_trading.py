"""CLI: Run all 3 trading bots for the current trading day.

Usage:
    # Dry run (logs only, no real orders):
    uv run python scripts/run_trading.py --dry-run

    # Live paper-trading execution:
    uv run python scripts/run_trading.py

    # Single bot:
    uv run python scripts/run_trading.py --bot ols
    uv run python scripts/run_trading.py --bot p0
    uv run python scripts/run_trading.py --bot pelosi
"""

from __future__ import annotations

import asyncio
import logging
import sys

import typer

from app.db import AsyncSessionLocal
from app.logging import setup_logging
from app.trading import model_bot, pelosi_bot, runner

setup_logging()
log = logging.getLogger("trading")

app = typer.Typer(pretty_exceptions_enable=False)


@app.command()
def main(
    dry_run: bool = typer.Option(False, "--dry-run", help="Log actions without placing orders"),
    bot: str = typer.Option("all", help="Which bot to run: all | ols | p0 | pelosi"),
    profile: str = typer.Option("P4_MOD_AGGRESSIVE", help="Portfolio profile for model bots"),
) -> None:
    async def _run() -> None:
        if bot == "all":
            async with AsyncSessionLocal() as session:
                results = await runner.run_all(session, dry_run=dry_run)

            print("\n" + "=" * 60)
            print(f"  TRADING SUMMARY  (dry_run={dry_run})")
            print("=" * 60)
            for r in results:
                status = "✓" if r.success else "✗"
                print(f"\n{status}  {r.name}")
                if r.error:
                    print(f"   ERROR: {r.error}")
                elif r.actions:
                    for a in r.actions:
                        if isinstance(a, dict):
                            print(f"   {a.get('type','?').upper():6} {a.get('ticker','?'):6}  ${a.get('amount',0):>8.2f}  [{a.get('source','')}]")
                        else:
                            if a.side != "skip":
                                print(f"   {a.side.upper():6} {a.ticker:6}  ${a.amount:>8.2f}  {a.reason}")
                else:
                    print("   No actions taken")
            print("=" * 60 + "\n")

        elif bot == "ols":
            async with AsyncSessionLocal() as session:
                actions = await model_bot.run(session, profile_id=profile, dry_run=dry_run)
            for a in actions:
                if a.side != "skip":
                    print(f"{a.side.upper():6} {a.ticker:6}  ${a.amount:>8.2f}  {a.reason}")

        elif bot == "p0":
            async with AsyncSessionLocal() as session:
                actions = await model_bot.run(
                    session,
                    profile_id="P0_ULTRA_CONSERVATIVE",
                    dry_run=dry_run,
                )
            for a in actions:
                if a.side != "skip":
                    print(f"{a.side.upper():6} {a.ticker:6}  ${a.amount:>8.2f}  {a.reason}")

        elif bot == "pelosi":
            actions = await pelosi_bot.run(dry_run=dry_run)
            for a in actions:
                print(f"{a.get('type','?').upper():6} {a.get('ticker','?'):6}  ${a.get('amount',0):>8.2f}")

        else:
            print(f"Unknown bot: {bot!r}. Choose: all | ols | p0 | pelosi", file=sys.stderr)
            raise typer.Exit(1)

    asyncio.run(_run())


if __name__ == "__main__":
    app()
