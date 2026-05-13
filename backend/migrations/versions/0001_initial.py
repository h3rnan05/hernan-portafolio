"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-04

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Required PG extensions
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "variables",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("unit", sa.String(32), nullable=True),
        sa.Column(
            "providers",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "kind IN ('predictor', 'stock', 'portfolio')",
            name="variables_kind_check",
        ),
    )

    op.create_table(
        "ingestion_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'running'"),
        ),
        sa.Column("rows_inserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'ok', 'partial', 'failed')",
            name="ingestion_runs_status_check",
        ),
    )

    op.create_table(
        "observations",
        sa.Column(
            "variable_id",
            sa.String(64),
            sa.ForeignKey("variables.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("observed_on", sa.Date(), primary_key=True),
        sa.Column("value", sa.Numeric(20, 8), nullable=False),
        sa.Column(
            "source_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ingestion_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("served_by_provider", sa.String(32), nullable=True),
    )
    op.create_index(
        "ix_obs_var_date_desc",
        "observations",
        ["variable_id", "observed_on"],
    )

    op.create_table(
        "models",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column(
            "fitted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("training_start", sa.Date(), nullable=False),
        sa.Column("training_end", sa.Date(), nullable=False),
        sa.Column("n_obs", sa.Integer(), nullable=False),
        sa.Column(
            "predictor_ids",
            postgresql.ARRAY(sa.String()),
            nullable=False,
        ),
        sa.Column("intercept", sa.Numeric(20, 8), nullable=False),
        sa.Column("coefficients", sa.JSON(), nullable=False),
        sa.Column("r2", sa.Numeric(10, 6), nullable=False),
        sa.Column("r2_adj", sa.Numeric(10, 6), nullable=False),
        sa.Column("durbin_watson", sa.Numeric(10, 6), nullable=False),
        sa.Column("breusch_pagan_p", sa.Numeric(10, 6), nullable=False),
        sa.Column("max_vif", sa.Numeric(10, 6), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.CheckConstraint(
            "status IN ('PASS', 'REVIEW', 'FAIL')",
            name="models_status_check",
        ),
    )
    op.create_index("ix_models_ticker", "models", ["ticker"])
    op.create_index(
        "uniq_active_model",
        "models",
        ["ticker"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )

    op.create_table(
        "predictions",
        sa.Column(
            "model_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("models.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("predicted_for", sa.Date(), primary_key=True),
        sa.Column(
            "predicted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("predicted_return", sa.Numeric(20, 8), nullable=True),
        sa.Column("predicted_price", sa.Numeric(20, 8), nullable=False),
        sa.Column("actual_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("abs_error_pct", sa.Numeric(10, 6), nullable=True),
    )
    op.create_index("ix_predictions_ticker", "predictions", ["ticker"])
    op.create_index("ix_predictions_predicted_for", "predictions", ["predicted_for"])

    op.create_table(
        "portfolios",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("weights", sa.JSON(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "positions_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "snapshot_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False),
        sa.Column("avg_price", sa.Numeric(20, 8), nullable=False),
        sa.Column("last_price", sa.Numeric(20, 8), nullable=False),
        sa.Column("market_value", sa.Numeric(20, 8), nullable=False),
        sa.Column("open_pnl", sa.Numeric(20, 8), nullable=False),
        sa.Column("open_pnl_pct", sa.Numeric(10, 6), nullable=False),
    )
    op.create_index(
        "ix_positions_snap_lookup",
        "positions_snapshots",
        ["snapshot_at", "ticker"],
    )


def downgrade() -> None:
    op.drop_table("positions_snapshots")
    op.drop_table("portfolios")
    op.drop_table("predictions")
    op.drop_table("models")
    op.drop_table("observations")
    op.drop_table("ingestion_runs")
    op.drop_table("variables")
