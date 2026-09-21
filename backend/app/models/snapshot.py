"""
Analysis job and snapshot models for the Environmental Twin.

Requirements: 7.1, 7.2, 1.3
"""

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, CreatedAtMixin, TimestampMixin, UUIDMixin, UTCTimestamp


# ---------------------------------------------------------------------------
# Job status enum
# ---------------------------------------------------------------------------


class JobStatus(str, PyEnum):
    """Analysis job status values.

    Requirements: 1.3, 1.5
    """

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


JobStatusType = Enum(
    JobStatus,
    native_enum=False,
    values_callable=lambda enum: [item.value for item in enum],
    length=20,
    name="jobstatus",
)


# ---------------------------------------------------------------------------
# AnalysisJob
# ---------------------------------------------------------------------------


class AnalysisJob(Base, UUIDMixin, TimestampMixin):
    """Durable background job that builds a Farm_Snapshot.

    Requirements: 1.1, 1.2, 1.3, 1.5, 7.1

    Each job is tied to a specific farm geometry revision.
    Status progresses forward-only: queued → running → completed | failed.
    Stages track per-provider progress as JSONB.
    """

    __tablename__ = "analysis_jobs"

    farm_id: Mapped[uuid.UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("farms.id", ondelete="CASCADE"),
        nullable=False,
    )

    geometry_revision: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[JobStatus] = mapped_column(
        JobStatusType,
        nullable=False,
        default=JobStatus.QUEUED,
    )

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Per-stage progress: {stage_name: {status, started_at, completed_at}}
    stages: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Set when job completes successfully
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        nullable=True,
    )

    # Relationships
    farm: Mapped["Farm"] = relationship("Farm", foreign_keys=[farm_id])
    snapshot: Mapped["AnalysisSnapshot | None"] = relationship(
        "AnalysisSnapshot",
        back_populates="job",
        foreign_keys="AnalysisSnapshot.job_id",
        uselist=False,
    )

    __table_args__ = (
        Index("ix_analysis_jobs_farm_status", "farm_id", "status"),
        Index("ix_analysis_jobs_farm_revision", "farm_id", "geometry_revision"),
    )


# ---------------------------------------------------------------------------
# AnalysisSnapshot
# ---------------------------------------------------------------------------


class AnalysisSnapshot(Base, UUIDMixin, CreatedAtMixin):
    """Immutable versioned record of all environmental evidence for a farm.

    Requirements: 7.1, 7.2, 7.3, 7.6

    Immutability is enforced at the database level by revoking UPDATE
    privilege from the runtime role. No field may change after creation.

    Each snapshot is bound to a specific (farm_id, geometry_revision) pair.
    Multiple immutable snapshots may reference the same revision; each job produces at most one.

    Environmental payloads use a consistent provenance envelope per value:
    {
        "value": <number | null>,
        "unit": "<string>",
        "source": "<provider name>",
        "acquired_at": "<ISO 8601 UTC>",
        "retrieved_at": "<ISO 8601 UTC>",
        "data_mode": "<live | historical_replay | demonstration>",
        "quality": "<accepted | unavailable | error>",
        "resolution_m": <number | null>
    }
    """

    __tablename__ = "analysis_snapshots"

    farm_id: Mapped[uuid.UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("farms.id", ondelete="RESTRICT"),
        nullable=False,
    )

    geometry_revision: Mapped[int] = mapped_column(Integer, nullable=False)

    job_id: Mapped[uuid.UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("analysis_jobs.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # UTC time the snapshot was calculated
    valid_time_utc: Mapped[UTCTimestamp]

    # mixed mode is "demonstration"; real data is "live" or "historical_replay"
    data_mode: Mapped[str] = mapped_column(String(50), nullable=False)

    # Environmental payload sections — all nullable (may be unavailable)
    weather: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    climate_baseline: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    satellite: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    soil: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    terrain: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    conduit: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Evidence status for every source — NOT NULL, always present
    # Keys: weather, satellite, soil, terrain, conduit, climate_baseline
    # Values: "accepted" | "unavailable" | "error" | "ineligible"
    evidence_statuses: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Model version string for this snapshot format
    model_version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="farmtwin-twin-v1",
    )

    # Relationships
    farm: Mapped["Farm"] = relationship("Farm", foreign_keys=[farm_id])
    job: Mapped["AnalysisJob"] = relationship(
        "AnalysisJob",
        back_populates="snapshot",
        foreign_keys=[job_id],
    )

    __table_args__ = (
        # Unique snapshot per farm revision (latest wins at lookup time)
        UniqueConstraint("job_id", name="uq_analysis_snapshots_job"),
        Index("ix_analysis_snapshots_farm_revision_time", "farm_id", "geometry_revision", "created_at"),
        # GIN index for filtering/searching evidence statuses
        Index(
            "ix_analysis_snapshots_evidence_statuses",
            "evidence_statuses",
            postgresql_using="gin",
        ),
        Index("ix_analysis_snapshots_farm_id", "farm_id"),
    )
