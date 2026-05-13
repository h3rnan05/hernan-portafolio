"""holdings table — user-editable portfolio positions

Distinct from ``positions_snapshots``: that table is append-only and
populated from Capital.com. This one is the user's own record of what
they hold, manually edited from the dashboard.

Revision ID: 0002_holdings
Revises: 0001_initial
Create Date: 2026-05-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_holdings"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "holdings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("ticker", sa.String(16), nullable=False, unique=True),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False),
        sa.Column("avg_price", sa.Numeric(20, 8), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("quantity >= 0", name="holdings_quantity_nonneg"),
        sa.CheckConstraint("avg_price >= 0", name="holdings_avg_price_nonneg"),
    )


def downgrade() -> None:
    op.drop_table("holdings")
