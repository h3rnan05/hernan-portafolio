"""trading_runs table

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008_trading_runs"
down_revision = "0007_variable_lag_transform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trading_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("ran_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("bot", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ok"),
        sa.Column("trades", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_trading_runs_ran_at", "trading_runs", ["ran_at"])


def downgrade() -> None:
    op.drop_table("trading_runs")
