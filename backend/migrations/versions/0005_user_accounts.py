"""user_accounts + account_holdings — named portfolios with risk-profile auto-classification

Revision ID: 0005_user_accounts
Revises: 0004_variable_is_target
Create Date: 2026-05-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_user_accounts"
down_revision: str | None = "0004_variable_is_target"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_accounts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("assigned_profile_id", sa.String(64), nullable=True),
        sa.Column("profile_assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "account_holdings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False),
        sa.Column("avg_price", sa.Numeric(20, 8), nullable=False),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["account_id"], ["user_accounts.id"], ondelete="CASCADE"),
        sa.CheckConstraint("quantity >= 0", name="account_holdings_quantity_nonneg"),
        sa.CheckConstraint("avg_price >= 0", name="account_holdings_avg_price_nonneg"),
        sa.UniqueConstraint("account_id", "ticker", name="uq_account_holdings_ticker"),
    )
    op.create_index(
        "ix_account_holdings_account_id",
        "account_holdings",
        ["account_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_account_holdings_account_id", table_name="account_holdings")
    op.drop_table("account_holdings")
    op.drop_table("user_accounts")
