"""models: estimator + alpha + resid_std (HER-16 / HER-17)

Adds the columns needed to (a) record which estimator produced a fit
(ols/ridge/lasso) and its regularization strength, and (b) store the residual
standard error so the forecast endpoint can build a confidence band without
re-fitting.

Revision ID: 0006_model_estimator
Revises: 0005_user_accounts
Create Date: 2026-06-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_model_estimator"
down_revision: str | None = "0005_user_accounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "models",
        sa.Column("resid_std", sa.Numeric(20, 8), nullable=True),
    )
    op.add_column(
        "models",
        sa.Column(
            "estimator",
            sa.String(8),
            nullable=False,
            server_default="ols",
        ),
    )
    op.add_column(
        "models",
        sa.Column("alpha", sa.Numeric(20, 8), nullable=True),
    )
    op.create_check_constraint(
        "models_estimator_check",
        "models",
        "estimator IN ('ols', 'ridge', 'lasso')",
    )


def downgrade() -> None:
    op.drop_constraint("models_estimator_check", "models")
    op.drop_column("models", "alpha")
    op.drop_column("models", "estimator")
    op.drop_column("models", "resid_std")
