"""variables.is_target — mark which variables are regression targets (Y)

Previously the refit pipeline hardcoded ``kind='stock'`` to identify the
dependent variables. This column decouples that logic: any active variable
with ``is_target=True`` becomes a regression target regardless of kind.

Revision ID: 0004_variable_is_target
Revises: 0003_portfolio_snapshots
Create Date: 2026-05-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_variable_is_target"
down_revision: str | None = "0003_portfolio_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "variables",
        sa.Column(
            "is_target",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # Backfill: all existing kind='stock' rows become targets
    op.execute(
        "UPDATE variables SET is_target = TRUE WHERE kind = 'stock'"
    )
    # Widen the kind check constraint to allow 'etf' and 'index' going forward
    op.drop_constraint("variables_kind_check", "variables")
    op.create_check_constraint(
        "variables_kind_check",
        "variables",
        "kind IN ('predictor', 'stock', 'etf', 'index', 'portfolio')",
    )


def downgrade() -> None:
    op.drop_constraint("variables_kind_check", "variables")
    op.create_check_constraint(
        "variables_kind_check",
        "variables",
        "kind IN ('predictor', 'stock', 'portfolio')",
    )
    op.drop_column("variables", "is_target")
