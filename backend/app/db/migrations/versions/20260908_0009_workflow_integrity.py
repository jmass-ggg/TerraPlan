"""Durable analysis leases, repeat snapshots, and honest plan evidence."""
from alembic import op
import sqlalchemy as sa
revision = "0009_workflow_integrity"
down_revision = "0008_climate_scenarios"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("uq_analysis_snapshots_farm_revision", "analysis_snapshots", type_="unique")
    op.create_unique_constraint("uq_analysis_snapshots_job", "analysis_snapshots", ["job_id"])
    op.create_index("ix_analysis_snapshots_farm_revision_time", "analysis_snapshots", ["farm_id", "geometry_revision", "created_at"])
    op.add_column("analysis_jobs", sa.Column("attempts", sa.Integer(), server_default="0", nullable=False))
    op.add_column("analysis_jobs", sa.Column("lease_until", sa.DateTime(timezone=True)))
    op.add_column("plan_entries", sa.Column("revision", sa.Integer(), server_default="1", nullable=False))
    op.add_column("plan_entries", sa.Column("field_name", sa.String(100)))
    for table, columns in [("plan_entries", ["suitability_index"]), ("change_proposals", ["old_suitability_index", "new_suitability_index"])]:
        for column in columns:
            op.alter_column(table, column, existing_type=sa.Integer(), nullable=True)
    # Historical ORM versions stored enum names while migrations used values.
    for table in ["analysis_jobs", "change_proposals", "ingestion_runs"]:
        op.execute(sa.text(f"UPDATE {table} SET status = lower(status)"))
    # Align all original Conduit timestamp columns with UTC-aware ORM types.
    bind = op.get_bind()
    for table in ["stations", "ingestion_runs", "normalized_observations", "hourly_aggregates", "daily_aggregates"]:
        for column in sa.inspect(bind).get_columns(table):
            if isinstance(column["type"], sa.DateTime) and not column["type"].timezone:
                name = column["name"]
                op.alter_column(table, name, type_=sa.DateTime(timezone=True), postgresql_using=f"{name} AT TIME ZONE 'UTC'")


def downgrade():
    # Repeated snapshots and nullable evidence cannot be discarded losslessly.
    raise RuntimeError("Restore a verified backup to downgrade this data-preserving migration.")
