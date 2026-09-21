"""Add plan_entries and change_proposals for Annual Planner.

Revision ID: 0007_annual_planner
Revises: 0006_hazard_action_completions
Create Date: 2026-09-08 00:07:00.000000

Requirements: 2.1, 4.1

Migration steps:
1. Create plan_entries table with FK to farms, date columns, cultivation info,
   snapshot reference, suitability_index, engine_version, and timestamps.
2. Create change_proposals table with FK to farms and plan_entries, old/new
   suitability indexes, changed_inputs JSONB, new_snapshot_id, issue_date, status.
3. Add B-tree indexes for common query patterns on both tables.
4. Create updated_at trigger for plan_entries (mutable).
5. Grant SELECT, INSERT, UPDATE, DELETE on both tables to the runtime role.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_annual_planner"
down_revision: Union[str, None] = "0006_hazard_action_completions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RUNTIME_ROLE = "farmtwin_runtime"


def upgrade() -> None:
    # ========================================================================
    # 1. Create plan_entries table
    # ========================================================================
    op.create_table(
        "plan_entries",
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
        sa.Column("crop_name", sa.String(length=100), nullable=False),
        sa.Column("planting_date", sa.Date(), nullable=False),
        # Derived and stored: planting_date + crop.duration_months
        sa.Column("harvest_date", sa.Date(), nullable=False),
        # "rainfed" | "irrigated"
        sa.Column("cultivation_mode", sa.String(length=50), nullable=False),
        # Only set when cultivation_mode = "irrigated"
        sa.Column("irrigation_mm", sa.Float(), nullable=True),
        # Area allocated to this entry (hectares)
        sa.Column("area_ha", sa.Float(), nullable=False),
        # Snapshot that was active when this entry was saved (null = demo mode)
        sa.Column(
            "snapshot_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        # "live" | "historical_replay" | "demonstration"
        sa.Column("data_mode", sa.String(length=50), nullable=False),
        # Suitability score [0–100] at save time
        sa.Column("suitability_index", sa.Integer(), nullable=False),
        # Crop engine version string
        sa.Column("engine_version", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_plan_entries"),
        sa.ForeignKeyConstraint(
            ["farm_id"],
            ["farms.id"],
            name="fk_plan_entries_farm_id_farms",
            ondelete="CASCADE",
        ),
        # harvest_date must be strictly after planting_date
        sa.CheckConstraint(
            "harvest_date > planting_date",
            name="ck_plan_entries_harvest_after_planting",
        ),
        # Suitability index in [0, 100]
        sa.CheckConstraint(
            "suitability_index >= 0 AND suitability_index <= 100",
            name="ck_plan_entries_suitability_range",
        ),
        # area_ha must be positive
        sa.CheckConstraint(
            "area_ha > 0",
            name="ck_plan_entries_area_positive",
        ),
    )

    # Fast per-farm entry lookups
    op.create_index(
        "ix_plan_entries_farm_id",
        "plan_entries",
        ["farm_id"],
    )

    # Overlap detection: covers farm_id + date range filter in a single scan
    op.create_index(
        "ix_plan_entries_farm_dates",
        "plan_entries",
        ["farm_id", "planting_date", "harvest_date"],
    )

    # updated_at auto-update trigger — plan_entries are mutable
    op.execute("""
        CREATE TRIGGER update_plan_entries_timestamp
        BEFORE UPDATE ON plan_entries
        FOR EACH ROW
        EXECUTE FUNCTION update_timestamp();
    """)

    # ========================================================================
    # 2. Create change_proposals table
    # ========================================================================
    op.create_table(
        "change_proposals",
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
        sa.Column(
            "entry_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("old_suitability_index", sa.Integer(), nullable=False),
        sa.Column("new_suitability_index", sa.Integer(), nullable=False),
        # Changed environmental inputs: {field: {old_value, new_value}}
        sa.Column(
            "changed_inputs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # The snapshot that triggered this proposal
        sa.Column(
            "new_snapshot_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        # When this proposal was generated
        sa.Column(
            "issue_date",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        # pending | accepted | dismissed
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_change_proposals"),
        sa.ForeignKeyConstraint(
            ["farm_id"],
            ["farms.id"],
            name="fk_change_proposals_farm_id_farms",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["plan_entries.id"],
            name="fk_change_proposals_entry_id_plan_entries",
            ondelete="CASCADE",
        ),
        # Suitability indexes in [0, 100]
        sa.CheckConstraint(
            "old_suitability_index >= 0 AND old_suitability_index <= 100",
            name="ck_change_proposals_old_suitability_range",
        ),
        sa.CheckConstraint(
            "new_suitability_index >= 0 AND new_suitability_index <= 100",
            name="ck_change_proposals_new_suitability_range",
        ),
        # Valid status values
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'dismissed')",
            name="ck_change_proposals_status_valid",
        ),
    )

    # Fast lookup of all proposals for a farm
    op.create_index(
        "ix_change_proposals_farm_id",
        "change_proposals",
        ["farm_id"],
    )

    # Proposals for a specific plan entry
    op.create_index(
        "ix_change_proposals_entry_id",
        "change_proposals",
        ["entry_id"],
    )

    # Filter pending proposals per farm efficiently
    op.create_index(
        "ix_change_proposals_farm_status",
        "change_proposals",
        ["farm_id", "status"],
    )

    # ========================================================================
    # 3. Grant runtime role permissions
    # ========================================================================
    # Both tables are mutable (entries are updated, proposals change status).
    # Runtime role needs full CRUD on both.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON plan_entries TO {RUNTIME_ROLE};
                GRANT SELECT, INSERT, UPDATE, DELETE ON change_proposals TO {RUNTIME_ROLE};
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    # Drop trigger first
    op.execute(
        "DROP TRIGGER IF EXISTS update_plan_entries_timestamp ON plan_entries"
    )

    # Drop change_proposals indexes
    op.drop_index("ix_change_proposals_farm_status", table_name="change_proposals")
    op.drop_index("ix_change_proposals_entry_id", table_name="change_proposals")
    op.drop_index("ix_change_proposals_farm_id", table_name="change_proposals")

    # Drop plan_entries indexes
    op.drop_index("ix_plan_entries_farm_dates", table_name="plan_entries")
    op.drop_index("ix_plan_entries_farm_id", table_name="plan_entries")

    # Drop tables — change_proposals first (FK to plan_entries)
    op.drop_table("change_proposals")
    op.drop_table("plan_entries")
