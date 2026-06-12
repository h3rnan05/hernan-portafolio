"""scenarios — named macro/geopolitical portfolios grouping the 5 risk profiles

Revision ID: 0008_scenarios
Revises: 0007_variable_lag_transform
Create Date: 2026-06-12

Phase 1 of the scenario-portfolios feature. PURELY ADDITIVE to existing data:

  * Creates the `scenarios` table.
  * Adds `portfolios.scenario_id` (FK) and `portfolios.profile_code` — both
    NULLABLE; no existing column is altered or dropped.
  * Seeds ONE scenario row ("Guerra en Medio Oriente", public, algorithmic,
    is_default=true) and tags the existing 5 profile rows to it by setting only
    the two NEW columns (`scenario_id`, `profile_code = id`).

The 5 existing portfolio rows keep their `id`, `name`, `description`, `weights`,
and `generated_at` exactly. `portfolio_snapshots` and `predictions` reference
portfolios by the unchanged `id`/`ticker`, so they are untouched. Fully
reversible — downgrade drops the new columns + table and the rows revert.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_scenarios"
down_revision: str | None = "0007_variable_lag_transform"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ─── New table: scenarios ────────────────────────────────────────────────
    op.create_table(
        "scenarios",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "scenario_type", sa.String(32), nullable=False,
            server_default="geopolitical",
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column(
            "build_mode", sa.String(16), nullable=False,
            server_default="algorithmic",
        ),
        sa.Column(
            "is_default", sa.Boolean, nullable=False, server_default=sa.false()
        ),
        sa.Column("display_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # At most one default scenario (partial unique index over is_default=true).
    op.create_index(
        "uq_scenarios_one_default",
        "scenarios",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )

    # ─── New, nullable columns on portfolios (no existing column touched) ─────
    op.add_column(
        "portfolios",
        sa.Column("scenario_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "portfolios",
        sa.Column("profile_code", sa.String(64), nullable=True),
    )
    op.create_foreign_key(
        "fk_portfolios_scenario_id",
        "portfolios",
        "scenarios",
        ["scenario_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ─── Seed the first scenario + tag the existing 5 profile rows ───────────
    # Atomic: insert the scenario, then set ONLY the two new columns on the
    # existing portfolios. `profile_code = id` copies the current id (e.g.
    # "P1_CONSERVATIVE") into the new column; the id itself is never modified.
    op.execute(
        sa.text(
            """
            WITH s AS (
                INSERT INTO scenarios
                    (slug, name, description, scenario_type, status,
                     build_mode, is_default, display_order)
                VALUES
                    ('mideast-oil',
                     'Guerra en Medio Oriente',
                     'Portafolio en vivo ante la guerra en Medio Oriente y la '
                     'volatilidad del petróleo. Reconstruido a diario por el '
                     'motor (perfiles P1–P5).',
                     'geopolitical', 'public', 'algorithmic', true, 0)
                RETURNING id
            )
            UPDATE portfolios
               SET scenario_id = (SELECT id FROM s),
                   profile_code = id
            """
        )
    )

    # Each scenario has exactly one of each profile.
    op.create_unique_constraint(
        "uq_portfolios_scenario_profile",
        "portfolios",
        ["scenario_id", "profile_code"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_portfolios_scenario_profile", "portfolios", type_="unique"
    )
    op.drop_constraint(
        "fk_portfolios_scenario_id", "portfolios", type_="foreignkey"
    )
    op.drop_column("portfolios", "profile_code")
    op.drop_column("portfolios", "scenario_id")
    op.drop_index("uq_scenarios_one_default", table_name="scenarios")
    op.drop_table("scenarios")
