"""variables: per-variable lag + transform (HER-15)

Two columns to fix the monthly-macro problem at the cause:

* ``lag_days`` — overrides the global 1-day lag for a single variable. Daily
  market series stay at lag 1; a slower-reacting series can use a longer lag.
  NULL means "use the global default".

* ``transform`` — how the raw observed level becomes a model input:
    - 'return'  : log return (or level-diff for series that go non-positive).
                  Correct for prices/indices/daily market data.
    - 'level'   : the forward-filled level itself, carried daily. This is the
                  fix for monthly macro: a 1-day-lagged *return* of a
                  forward-filled monthly series is zero ~95% of days (one
                  impulse per release), so it carries almost no information.
                  The level is a persistent, non-zero daily regressor.
    - 'surprise': release-day change only (published − previous), zero between
                  releases — the classic event-study encoding.

Revision ID: 0007_variable_lag_transform
Revises: 0006_model_estimator
Create Date: 2026-06-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_variable_lag_transform"
down_revision: str | None = "0006_model_estimator"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("variables", sa.Column("lag_days", sa.SmallInteger(), nullable=True))
    op.add_column(
        "variables",
        sa.Column(
            "transform",
            sa.String(16),
            nullable=False,
            server_default="return",
        ),
    )
    op.create_check_constraint(
        "variables_transform_check",
        "variables",
        "transform IN ('return', 'level', 'surprise')",
    )


def downgrade() -> None:
    op.drop_constraint("variables_transform_check", "variables")
    op.drop_column("variables", "transform")
    op.drop_column("variables", "lag_days")
