"""Add saved_scenarios table for Climate Scenario Explorer.

Revision ID: 0008_climate_scenarios
Revises: 0007_annual_planner
Create Date: 2026-09-08 00:08:00.000000

Requirements: 2.5, 2.7

Migration steps:
1. Create saved_scenarios table with UUID PK, farm_id FK, name, baseline_snapshot_id FK,
   delta JSONB, results JSONB, engine_version, and created_at.
2. Add B-tree indexes for common query patterns (farm_id, farm_id + created_at).
3. Grant SELECT, INSERT on saved_scenarios to runtime role (immutable — no UPDATE).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_climate_scenarios"
down_revision: Union[str, None] = "0007_annual_planner"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RUNTIME_ROLE = "farmtwin_runtime"


def upgrade() -> None:
    # ========================================================================
    # 1. Create saved_scenarios table (immutable after INSERT)
    # ========================================================================
    op.create_table(
        "saved_scenarios",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "farm_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        # The baseline snapshot this scenario was derived from.
        # SET NULL on delete so historical scenarios survive snapshot cleanup.
        sa.Column(
            "baseline_snapshot_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        # Delta parameters: {rainfall_change_pct, temperature_change_c, irrigation_mm_override}
        sa.Column(
            "delta",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        # Computed results: {crops: [...], hazards: [...]}
        sa.Column(
            "results",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        # Engine version string for reproducibility
        sa.Column("engine_version", sa.String(length=100), nullable=False),
        # Immutable — only created_at, no updated_at
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_saved_scenarios"),
        # CASCADE: removing a farm removes all its saved scenarios
        sa.ForeignKeyConstraint(
            ["farm_id"],
            ["farms.id"],
            name="fk_saved_scenarios_farm_id_farms",
            ondelete="CASCADE",
        ),
        # SET NULL: snapshots can be cleaned up independently
        sa.ForeignKeyConstraint(
            ["baseline_snapshot_id"],
            ["analysis_snapshots.id"],
            name="fk_saved_scenarios_baseline_snapshot_id_analysis_snapshots",
            ondelete="SET NULL",
        ),
    )

    # ========================================================================
    # 2. Create indexes
    # ========================================================================
    # Fast per-farm scenario lookup
    op.create_index(
        "ix_saved_scenarios_farm_id",
        "saved_scenarios",
        ["farm_id"],
    )

    # Ordered listing: GET /farms/{id}/scenarios sorted by created_at
    op.create_index(
        "ix_saved_scenarios_farm_created",
        "saved_scenarios",
        ["farm_id", "created_at"],
    )

    # ========================================================================
    # 3. Grant runtime role permissions
    # ========================================================================
    # saved_scenarios is immutable — SELECT and INSERT only, no UPDATE or DELETE
    # (Requirement 2.7: saved scenario is never overwritten)
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
                GRANT SELECT, INSERT ON saved_scenarios TO {RUNTIME_ROLE};
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    # Drop indexes
    op.drop_index("ix_saved_scenarios_farm_created", table_name="saved_scenarios")
    op.drop_index("ix_saved_scenarios_farm_id", table_name="saved_scenarios")

    # Drop table
    op.drop_table("saved_scenarios")
