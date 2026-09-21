"""Conduit pipeline tables: stations, ingestion_runs, normalized_observations,
hourly_aggregates, daily_aggregates.

Revision ID: 0002_conduit_pipeline
Revises: 0001_initial
Create Date: 2025-09-06 00:02:00.000000

Requirements: 1.2, 4.7, 5.1, 6.4

Migration order:
1. Create stations table
2. Create ingestion_runs table
3. Create normalized_observations with unique constraint and indexes
4. Create hourly_aggregates and daily_aggregates with unique constraints
5. Grant SELECT/INSERT on all new tables to runtime role
6. Revoke UPDATE/DELETE on normalized_observations (immutable records)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_conduit_pipeline"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Runtime role that owns SELECT/INSERT access
RUNTIME_ROLE = "farmtwin_runtime"

# Quality flag and ingestion status VARCHAR values
QUALITY_FLAG_VALUES = (
    "accepted", "suspect", "missing", "stale", "invalid", "single_channel", "unconfirmed"
)
INGESTION_STATUS_VALUES = ("running", "completed", "failed", "duplicate_run")


def upgrade() -> None:
    # ========================================================================
    # 1. Create stations table
    # ========================================================================
    op.create_table(
        "stations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("provider_station_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("elevation_m", sa.Float(), nullable=True),
        sa.Column("provider", sa.String(100), nullable=False, server_default="conduit"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stations"),
        sa.UniqueConstraint("provider_station_id", name="uq_stations_provider_station_id"),
    )

    op.execute("""
        CREATE TRIGGER update_stations_timestamp
        BEFORE UPDATE ON stations
        FOR EACH ROW
        EXECUTE FUNCTION update_timestamp();
    """)

    # ========================================================================
    # 2. Create ingestion_runs table
    # ========================================================================
    op.create_table(
        "ingestion_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column(
            "retrieval_time",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')"),
        ),
        sa.Column("payload_size_bytes", sa.Integer(), nullable=True),
        sa.Column("sha256_checksum", sa.String(64), nullable=True),
        sa.Column("parser_version", sa.String(50), nullable=True),
        sa.Column("normalizer_version", sa.String(50), nullable=True),
        sa.Column("accepted_count", sa.Integer(), nullable=True),
        sa.Column("rejected_count", sa.Integer(), nullable=True),
        sa.Column("duplicate_count", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="running",
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ingestion_runs"),
    )

    op.create_index(
        "ix_ingestion_runs_sha256_checksum",
        "ingestion_runs",
        ["sha256_checksum"],
        unique=False,
    )

    # ========================================================================
    # 3. Create normalized_observations table
    # ========================================================================
    op.create_table(
        "normalized_observations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("station_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ingestion_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "valid_time_utc",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')"),
        ),
        sa.Column("data_mode", sa.String(30), nullable=False),
        # Temperature channels
        sa.Column("temp_bmx_celsius", sa.Float(), nullable=True),
        sa.Column("temp_bmx_quality", sa.String(20), nullable=False),
        sa.Column("temp_mcp_celsius", sa.Float(), nullable=True),
        sa.Column("temp_mcp_quality", sa.String(20), nullable=False),
        sa.Column("temp_sht_celsius", sa.Float(), nullable=True),
        sa.Column("temp_sht_quality", sa.String(20), nullable=False),
        # Temperature consensus
        sa.Column("temperature_consensus", sa.Float(), nullable=True),
        sa.Column("temperature_consensus_quality", sa.String(20), nullable=False),
        sa.Column(
            "temperature_channel_count",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        # Humidity
        sa.Column("humidity_sht_pct", sa.Float(), nullable=True),
        sa.Column("humidity_sht_quality", sa.String(20), nullable=False),
        # VPD
        sa.Column("vpd_kpa", sa.Float(), nullable=True),
        sa.Column("vpd_quality", sa.String(20), nullable=False),
        # Wind
        sa.Column("wind_spd_ms", sa.Float(), nullable=True),
        sa.Column("wind_spd_quality", sa.String(20), nullable=False),
        sa.Column("wind_gust_ms", sa.Float(), nullable=True),
        sa.Column("wind_gust_quality", sa.String(20), nullable=False),
        sa.Column("wind_dir_deg", sa.Float(), nullable=True),
        sa.Column("wind_dir_quality", sa.String(20), nullable=False),
        # Pressure
        sa.Column("press_hpa", sa.Float(), nullable=True),
        sa.Column("press_quality", sa.String(20), nullable=False),
        # Light / UV (uncalibrated raw counts)
        sa.Column("si1145_vis_raw", sa.Float(), nullable=True),
        sa.Column("si1145_vis_quality", sa.String(20), nullable=False),
        sa.Column("si1145_ir_raw", sa.Float(), nullable=True),
        sa.Column("si1145_ir_quality", sa.String(20), nullable=False),
        sa.Column("si1145_uv_raw", sa.Float(), nullable=True),
        sa.Column("si1145_uv_quality", sa.String(20), nullable=False),
        # Rainfall (unconfirmed semantics — Requirements 4.7)
        sa.Column("rg1_raw", sa.Float(), nullable=True),
        sa.Column("rg1_quality", sa.String(20), nullable=False, server_default="unconfirmed"),
        sa.Column("rg2_raw", sa.Float(), nullable=True),
        sa.Column("rg2_quality", sa.String(20), nullable=False, server_default="unconfirmed"),
        sa.Column("rg1tt_raw", sa.Float(), nullable=True),
        sa.Column("rg1tt_quality", sa.String(20), nullable=False, server_default="unconfirmed"),
        sa.Column("rg2tt_raw", sa.Float(), nullable=True),
        sa.Column("rg2tt_quality", sa.String(20), nullable=False, server_default="unconfirmed"),
        sa.Column("rg1tp_raw", sa.Float(), nullable=True),
        sa.Column("rg1tp_quality", sa.String(20), nullable=False, server_default="unconfirmed"),
        sa.Column("rg2tp_raw", sa.Float(), nullable=True),
        sa.Column("rg2tp_quality", sa.String(20), nullable=False, server_default="unconfirmed"),
        # Timestamp (immutable)
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_normalized_observations"),
        # Foreign keys
        sa.ForeignKeyConstraint(
            ["station_id"],
            ["stations.id"],
            name="fk_normalized_observations_station_id_stations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["ingestion_runs.id"],
            name="fk_normalized_observations_ingestion_run_id_ingestion_runs",
            ondelete="RESTRICT",
        ),
        # Named unique constraint — Requirements 6.4
        sa.UniqueConstraint(
            "station_id",
            "valid_time_utc",
            name="uq_observation_station_valid_time",
        ),
    )

    op.create_index(
        "ix_normalized_observations_station_valid_time",
        "normalized_observations",
        ["station_id", "valid_time_utc"],
        unique=False,
    )
    op.create_index(
        "ix_normalized_observations_ingestion_run",
        "normalized_observations",
        ["ingestion_run_id"],
        unique=False,
    )

    # ========================================================================
    # 4. Create hourly_aggregates table
    # ========================================================================
    op.create_table(
        "hourly_aggregates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("station_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "window_start_utc",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')"),
        ),
        sa.Column("data_mode", sa.String(30), nullable=False),
        sa.Column("temp_mean_celsius", sa.Float(), nullable=True),
        sa.Column("temp_min_celsius", sa.Float(), nullable=True),
        sa.Column("temp_max_celsius", sa.Float(), nullable=True),
        sa.Column("humidity_mean_pct", sa.Float(), nullable=True),
        sa.Column("humidity_min_pct", sa.Float(), nullable=True),
        sa.Column("humidity_max_pct", sa.Float(), nullable=True),
        sa.Column("wind_spd_mean_ms", sa.Float(), nullable=True),
        sa.Column("wind_spd_min_ms", sa.Float(), nullable=True),
        sa.Column("wind_spd_max_ms", sa.Float(), nullable=True),
        sa.Column("vpd_mean_kpa", sa.Float(), nullable=True),
        sa.Column(
            "observation_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "accepted_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "coverage_ratio",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.0"),
        ),
        sa.Column(
            "source_observation_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hourly_aggregates"),
        sa.ForeignKeyConstraint(
            ["station_id"],
            ["stations.id"],
            name="fk_hourly_aggregates_station_id_stations",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "station_id",
            "window_start_utc",
            name="uq_hourly_aggregates_station_window",
        ),
    )

    op.create_index(
        "ix_hourly_aggregates_station_window",
        "hourly_aggregates",
        ["station_id", "window_start_utc"],
        unique=False,
    )

    op.execute("""
        CREATE TRIGGER update_hourly_aggregates_timestamp
        BEFORE UPDATE ON hourly_aggregates
        FOR EACH ROW
        EXECUTE FUNCTION update_timestamp();
    """)

    # ========================================================================
    # 5. Create daily_aggregates table
    # ========================================================================
    op.create_table(
        "daily_aggregates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("station_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "window_start_utc",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')"),
        ),
        sa.Column("data_mode", sa.String(30), nullable=False),
        sa.Column("temp_mean_celsius", sa.Float(), nullable=True),
        sa.Column("temp_min_celsius", sa.Float(), nullable=True),
        sa.Column("temp_max_celsius", sa.Float(), nullable=True),
        sa.Column("humidity_mean_pct", sa.Float(), nullable=True),
        sa.Column("humidity_min_pct", sa.Float(), nullable=True),
        sa.Column("humidity_max_pct", sa.Float(), nullable=True),
        sa.Column("wind_spd_mean_ms", sa.Float(), nullable=True),
        sa.Column("wind_spd_min_ms", sa.Float(), nullable=True),
        sa.Column("wind_spd_max_ms", sa.Float(), nullable=True),
        sa.Column("vpd_mean_kpa", sa.Float(), nullable=True),
        sa.Column(
            "observation_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "accepted_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "coverage_ratio",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.0"),
        ),
        sa.Column(
            "source_observation_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_daily_aggregates"),
        sa.ForeignKeyConstraint(
            ["station_id"],
            ["stations.id"],
            name="fk_daily_aggregates_station_id_stations",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "station_id",
            "window_start_utc",
            name="uq_daily_aggregates_station_window",
        ),
    )

    op.create_index(
        "ix_daily_aggregates_station_window",
        "daily_aggregates",
        ["station_id", "window_start_utc"],
        unique=False,
    )

    op.execute("""
        CREATE TRIGGER update_daily_aggregates_timestamp
        BEFORE UPDATE ON daily_aggregates
        FOR EACH ROW
        EXECUTE FUNCTION update_timestamp();
    """)

    # ========================================================================
    # 6. Grant SELECT/INSERT to runtime role on all new tables
    #    Deny UPDATE/DELETE on normalized_observations (immutable)
    # ========================================================================
    new_tables = [
        "stations",
        "ingestion_runs",
        "normalized_observations",
        "hourly_aggregates",
        "daily_aggregates",
    ]

    for table in new_tables:
        op.execute(
            f"GRANT SELECT, INSERT ON {table} TO {RUNTIME_ROLE}"
        )

    # Mutable aggregate tables also need UPDATE (re-computed on re-ingestion)
    op.execute(
        f"GRANT UPDATE ON hourly_aggregates TO {RUNTIME_ROLE}"
    )
    op.execute(
        f"GRANT UPDATE ON daily_aggregates TO {RUNTIME_ROLE}"
    )

    # Explicitly revoke UPDATE/DELETE on normalized_observations — immutable records
    op.execute(
        "REVOKE UPDATE, DELETE ON normalized_observations FROM PUBLIC"
    )


def downgrade() -> None:
    # Drop triggers
    op.execute("DROP TRIGGER IF EXISTS update_daily_aggregates_timestamp ON daily_aggregates")
    op.execute("DROP TRIGGER IF EXISTS update_hourly_aggregates_timestamp ON hourly_aggregates")
    op.execute("DROP TRIGGER IF EXISTS update_stations_timestamp ON stations")

    # Drop indexes
    op.drop_index("ix_daily_aggregates_station_window", table_name="daily_aggregates")
    op.drop_index("ix_hourly_aggregates_station_window", table_name="hourly_aggregates")
    op.drop_index("ix_normalized_observations_ingestion_run", table_name="normalized_observations")
    op.drop_index("ix_normalized_observations_station_valid_time", table_name="normalized_observations")
    op.drop_index("ix_ingestion_runs_sha256_checksum", table_name="ingestion_runs")

    # Drop tables in reverse dependency order
    op.drop_table("daily_aggregates")
    op.drop_table("hourly_aggregates")
    op.drop_table("normalized_observations")
    op.drop_table("ingestion_runs")
    op.drop_table("stations")
