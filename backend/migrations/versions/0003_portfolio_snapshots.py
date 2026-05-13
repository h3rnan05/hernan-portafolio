"""portfolio_snapshots — append-only history of each portfolio profile rebuild

Distinct from `portfolios` (current state, UPSERT-overwritten each cron run).
This table preserves every snapshot so we can chart weight evolution + MAPE
drift over time.

Revision ID: 0003_portfolio_snapshots
Revises: 0002_holdings
Create Date: 2026-05-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_portfolio_snapshots"
down_revision: str | None = "0002_holdings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "portfolio_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("portfolio_id", sa.String(64), nullable=False),
        sa.Column("weights", sa.JSON(), nullable=False),
        sa.Column("mape_30d", sa.Numeric(10, 6), nullable=True),
        sa.Column(
            "snapshotted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_portfolio_snapshots_lookup",
        "portfolio_snapshots",
        ["portfolio_id", "snapshotted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_portfolio_snapshots_lookup", table_name="portfolio_snapshots")
    op.drop_table("portfolio_snapshots")
