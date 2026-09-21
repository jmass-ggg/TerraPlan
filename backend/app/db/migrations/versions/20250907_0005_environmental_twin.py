"""Add analysis_jobs and analysis_snapshots for Environmental Twin.

Revision ID: 0005_environmental_twin
Revises: 0004_runtime_permissions
Create Date: 2026-09-07 00:05:00.000000

Requirements: 7.1, 7.2

Migration order:
1. Create analysis_jobs table with compound indexes on (farm_id, status) and (farm_id, geometry_revision)
2. Create analysis_snapshots table with:
   - Unique index on (farm_id, geometry_revision)
   - GIN index on evidence_statuses
3. Grant SELECT, INSERT, UPDATE on analysis_jobs to runtime role
4. Grant SELECT, INSERT on analysis_snapshots to runtime role (immutable — no UPDATE)
5. Revoke UPDATE on analysis_snapshots from PUBLIC
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_environmental_twin"
down_revision: Union[str, None] = "0004_runtime_permissions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RUNTIME_ROLE = "farmtwin_runtime"


def upgrade() -> None:
    # ========================================================================
    # 1. Create analysis_jobs table
    # ========================================================================
    op.create_table(
        "analysis_jobs",
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
        sa.Column("geometry_revision", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="queued",
        ),
        # Per-stage progress: {stage_name: {status, started_at, completed_at}}
        sa.Column(
            "stages",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        # Set on completion — references analysis_snapshots.id (added via FK below)
        sa.Column(
            "snapshot_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
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
        sa.PrimaryKeyConstraint("id", name="pk_analysis_jobs"),
        sa.ForeignKeyConstraint(
            ["farm_id"],
            ["farms.id"],
            name="fk_analysis_jobs_farm_id_farms",
            ondelete="CASCADE",
        ),
    )

    # Indexes required by Requirements 7.1, 7.2:
    # - Fast lookup of jobs by farm + status (e.g. "any running jobs for this farm?")
    # - Fast lookup of jobs by farm + revision (e.g. "job for revision 3?")
    op.create_index(
        "ix_analysis_jobs_farm_status",
        "analysis_jobs",
        ["farm_id", "status"],
    )
    op.create_index(
        "ix_analysis_jobs_farm_revision",
        "analysis_jobs",
        ["farm_id", "geometry_revision"],
    )

    # updated_at trigger — jobs are mutable (status transitions)
    op.execute("""
        CREATE TRIGGER update_analysis_jobs_timestamp
        BEFORE UPDATE ON analysis_jobs
        FOR EACH ROW
        EXECUTE FUNCTION update_timestamp();
    """)

    # ========================================================================
    # 2. Create analysis_snapshots table (immutable after INSERT)
    # ========================================================================
    op.create_table(
        "analysis_snapshots",
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
        sa.Column("geometry_revision", sa.Integer(), nullable=False),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        # UTC time the snapshot was calculated — NOT NULL, never null
        sa.Column(
            "valid_time_utc",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        # mixed/"demonstration", "live", "historical_replay"
        sa.Column("data_mode", sa.String(length=50), nullable=False),
        # Environmental payload sections — all nullable (may be unavailable)
        sa.Column(
            "weather",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "climate_baseline",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "satellite",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "soil",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "terrain",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "conduit",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        # evidence_statuses is NOT NULL — always present, even for all-unavailable snapshots
        # Keys: weather, satellite, soil, terrain, conduit, climate_baseline
        # Values: "accepted" | "unavailable" | "error" | "ineligible"
        sa.Column(
            "evidence_statuses",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "model_version",
            sa.String(length=50),
            nullable=False,
            server_default="farmtwin-twin-v1",
        ),
        # Immutable — only created_at, no updated_at
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_analysis_snapshots"),
        # ON DELETE RESTRICT: blocks farm deletion when snapshots exist
        sa.ForeignKeyConstraint(
            ["farm_id"],
            ["farms.id"],
            name="fk_analysis_snapshots_farm_id_farms",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["analysis_jobs.id"],
            name="fk_analysis_snapshots_job_id_analysis_jobs",
            ondelete="RESTRICT",
        ),
        # One snapshot per (farm, geometry revision) — Requirements 7.2
        sa.UniqueConstraint(
            "farm_id",
            "geometry_revision",
            name="uq_analysis_snapshots_farm_revision",
        ),
    )

    # GIN index on evidence_statuses for efficient filtering
    # e.g. "find snapshots where satellite is unavailable"
    op.create_index(
        "ix_analysis_snapshots_evidence_statuses",
        "analysis_snapshots",
        ["evidence_statuses"],
        postgresql_using="gin",
    )

    # Supporting index for the common "latest snapshot for farm" query
    op.create_index(
        "ix_analysis_snapshots_farm_id",
        "analysis_snapshots",
        ["farm_id"],
    )

    # ========================================================================
    # 3. Add back-reference FK from analysis_jobs.snapshot_id
    # ========================================================================
    # Added after analysis_snapshots exists so there's no circular dependency
    # issue in the table creation step above.
    op.create_foreign_key(
        "fk_analysis_jobs_snapshot_id_analysis_snapshots",
        "analysis_jobs",
        "analysis_snapshots",
        ["snapshot_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ========================================================================
    # 4. Grant runtime role permissions
    # ========================================================================
    # analysis_jobs: mutable — needs SELECT, INSERT, UPDATE (status transitions)
    # analysis_snapshots: immutable — SELECT and INSERT only, no UPDATE
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
                GRANT SELECT, INSERT, UPDATE ON analysis_jobs TO {RUNTIME_ROLE};
                GRANT SELECT, INSERT ON analysis_snapshots TO {RUNTIME_ROLE};
            END IF;
        END
        $$;
        """
    )

    # ========================================================================
    # 5. Revoke UPDATE on analysis_snapshots — snapshots are immutable
    # ========================================================================
    # Requirements: 7.2 — AnalysisSnapshot is immutable; no UPDATE after creation.
    op.execute("REVOKE UPDATE ON analysis_snapshots FROM PUBLIC;")


def downgrade() -> None:
    # Drop FK back-reference first (it references analysis_snapshots)
    op.drop_constraint(
        "fk_analysis_jobs_snapshot_id_analysis_snapshots",
        "analysis_jobs",
        type_="foreignkey",
    )

    # Drop trigger
    op.execute(
        "DROP TRIGGER IF EXISTS update_analysis_jobs_timestamp ON analysis_jobs"
    )

    # Drop indexes on analysis_snapshots
    op.drop_index("ix_analysis_snapshots_farm_id", table_name="analysis_snapshots")
    op.drop_index(
        "ix_analysis_snapshots_evidence_statuses",
        table_name="analysis_snapshots",
        postgresql_using="gin",
    )

    # Drop indexes on analysis_jobs
    op.drop_index("ix_analysis_jobs_farm_revision", table_name="analysis_jobs")
    op.drop_index("ix_analysis_jobs_farm_status", table_name="analysis_jobs")

    # Drop tables — snapshots first (FK points to jobs)
    op.drop_table("analysis_snapshots")
    op.drop_table("analysis_jobs")
