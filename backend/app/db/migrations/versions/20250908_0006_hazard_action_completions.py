"""Add action_completions table for Farm Risk Center.

Revision ID: 0006_hazard_action_completions
Revises: 0005_environmental_twin
Create Date: 2026-09-08 00:06:00.000000

Requirements: 7.4, 7.5

Migration steps:
1. Create action_completions table with UUID PK, farm_id FK, action_id VARCHAR(100),
   completed_at TIMESTAMPTZ, created_at TIMESTAMPTZ.
2. Add unique constraint on (farm_id, action_id).
3. Add B-tree index on farm_id for fast per-farm lookups.
4. Grant SELECT, INSERT, UPDATE on action_completions to runtime role.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_hazard_action_completions"
down_revision: Union[str, None] = "0005_environmental_twin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RUNTIME_ROLE = "farmtwin_runtime"


def upgrade() -> None:
    # ========================================================================
    # 1. Create action_completions table
    # ========================================================================
    op.create_table(
        "action_completions",
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
        # Rule identifier from rules_v1.yaml, e.g. "drought-irrigate-or-mulch"
        sa.Column("action_id", sa.String(length=100), nullable=False),
        # When the farmer marked this action as done
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_action_completions"),
        # Cascade delete when the farm is removed
        sa.ForeignKeyConstraint(
            ["farm_id"],
            ["farms.id"],
            name="fk_action_completions_farm_id_farms",
            ondelete="CASCADE",
        ),
        # One completion record per (farm, action) — idempotent upsert target
        sa.UniqueConstraint(
            "farm_id",
            "action_id",
            name="uq_action_completions_farm_action",
        ),
    )

    # ========================================================================
    # 2. B-tree index on farm_id for efficient per-farm queries
    # ========================================================================
    op.create_index(
        "ix_action_completions_farm_id",
        "action_completions",
        ["farm_id"],
    )

    # ========================================================================
    # 3. Grant runtime role permissions
    # ========================================================================
    # SELECT, INSERT, UPDATE needed:
    #   - SELECT: read completion state when assembling RiskResponse
    #   - INSERT: first completion of an action
    #   - UPDATE: idempotent upsert (ON CONFLICT DO UPDATE) updates completed_at
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
                GRANT SELECT, INSERT, UPDATE ON action_completions TO {RUNTIME_ROLE};
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_index("ix_action_completions_farm_id", table_name="action_completions")
    op.drop_table("action_completions")
