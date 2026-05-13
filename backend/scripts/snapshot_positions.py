"""CLI: snapshot Capital.com positions into ``positions_snapshots``.

Usage:
    uv run python scripts/snapshot_positions.py
"""

from __future__ import annotations

import asyncio

import structlog
import typer

from app.db import AsyncSessionLocal
from app.ingestion.positions_snapshot import snapshot_positions
from app.logging import setup_logging

setup_logging()
log = structlog.get_logger(__name__)

app = typer.Typer(pretty_exceptions_enable=False)


@app.command()
def main() -> None:
    async def _run() -> None:
        async with AsyncSessionLocal() as session:
            n = await snapshot_positions(session)
        print(f"Snapshotted {n} positions")

    asyncio.run(_run())


if __name__ == "__main__":
    app()
