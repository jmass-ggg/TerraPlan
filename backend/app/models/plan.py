"""
PlanEntry and ChangeProposal models for the Annual Planner.

Requirements: 2.1, 4.1
"""

import uuid
from datetime import date
from enum import Enum as PyEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin, UTCTimestamp


# ---------------------------------------------------------------------------
# Change proposal status enum
# ---------------------------------------------------------------------------


class ProposalStatus(str, PyEnum):
    """Status values for a ChangeProposal.

    Requirements: 4.1
    """

    PENDING = "pending"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"


ProposalStatusType = Enum(
    ProposalStatus,
    native_enum=False,
    values_callable=lambda enum: [item.value for item in enum],
    length=20,
    name="proposalstatus",
)


# ---------------------------------------------------------------------------
# PlanEntry
# ---------------------------------------------------------------------------


class PlanEntry(Base, UUIDMixin, TimestampMixin):
    """
    A single approved crop allocation in a farm's annual plan.

    Requirements: 2.1

    Captures: crop, planting window, cultivation mode, area fraction,
    the snapshot used when the entry was created, suitability score,
    and the engine version that generated it.

    harvest_date is derived from planting_date + crop.duration_months
    (computed by PlannerService before INSERT; stored for fast queries).

    Overlap validation: no two entries for the same farm may have
    [planting_date, harvest_date] intervals that intersect.  Enforced
    in PlannerService.create_entry / update_entry (Requirements 2.2, 3.3).
    """

    __tablename__ = "plan_entries"

    farm_id: Mapped[uuid.UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("farms.id", ondelete="CASCADE"),
        nullable=False,
    )

    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    field_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    crop_name: Mapped[str] = mapped_column(String(100), nullable=False)

    planting_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Derived from planting_date + crop.duration_months; stored for queries
    harvest_date: Mapped[date] = mapped_column(Date, nullable=False)

    # "rainfed" | "irrigated"
    cultivation_mode: Mapped[str] = mapped_column(String(50), nullable=False)

    # Only populated when cultivation_mode == "irrigated"
    irrigation_mm: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Fraction of the farm area allocated to this entry (in hectares)
    area_ha: Mapped[float] = mapped_column(Float, nullable=False)

    # Environmental snapshot used at save time (null when demo mode)
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        nullable=True,
    )

    # "live" | "historical_replay" | "demonstration"
    data_mode: Mapped[str] = mapped_column(String(50), nullable=False)

    # Suitability score [0–100] from crop engine at save time
    suitability_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Crop engine version that generated this recommendation
    engine_version: Mapped[str] = mapped_column(String(50), nullable=False)

    # Relationships
    farm: Mapped["Farm"] = relationship("Farm", foreign_keys=[farm_id])
    proposals: Mapped[list["ChangeProposal"]] = relationship(
        "ChangeProposal",
        back_populates="entry",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        # harvest_date must be after planting_date
        CheckConstraint(
            "harvest_date > planting_date",
            name="ck_plan_entries_harvest_after_planting",
        ),
        # suitability_index in [0, 100]
        CheckConstraint(
            "suitability_index >= 0 AND suitability_index <= 100",
            name="ck_plan_entries_suitability_range",
        ),
        # area_ha must be positive
        CheckConstraint(
            "area_ha > 0",
            name="ck_plan_entries_area_positive",
        ),
        # Fast per-farm entry lookups (most common query pattern)
        Index("ix_plan_entries_farm_id", "farm_id"),
        # Overlap detection: scan entries by farm + date range
        Index("ix_plan_entries_farm_dates", "farm_id", "planting_date", "harvest_date"),
    )


# ---------------------------------------------------------------------------
# ChangeProposal
# ---------------------------------------------------------------------------


class ChangeProposal(Base, UUIDMixin):
    """
    System-generated suggestion to update a saved PlanEntry when material
    environmental inputs change.

    Requirements: 4.1

    Created by the ingestion pipeline when a new snapshot for the farm
    differs materially (|new_index - old_index| >= 5) from the snapshot
    used when the PlanEntry was saved.

    Immutable after creation (status transitions are the only mutation,
    handled by accept_proposal / dismiss logic in PlannerService).
    """

    __tablename__ = "change_proposals"

    farm_id: Mapped[uuid.UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("farms.id", ondelete="CASCADE"),
        nullable=False,
    )

    entry_id: Mapped[uuid.UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("plan_entries.id", ondelete="CASCADE"),
        nullable=False,
    )

    old_suitability_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_suitability_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Environmental inputs that changed: {field: {old_value, new_value}}
    changed_inputs: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default="{}",
    )

    # The snapshot that triggered this proposal
    new_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        nullable=False,
    )

    # When this proposal was generated
    issue_date: Mapped[UTCTimestamp]

    # pending | accepted | dismissed
    status: Mapped[ProposalStatus] = mapped_column(
        ProposalStatusType,
        nullable=False,
        default=ProposalStatus.PENDING,
        server_default="pending",
    )

    created_at: Mapped[UTCTimestamp]

    # Relationships
    farm: Mapped["Farm"] = relationship("Farm", foreign_keys=[farm_id])
    entry: Mapped["PlanEntry"] = relationship("PlanEntry", back_populates="proposals")

    __table_args__ = (
        # suitability indexes in [0, 100]
        CheckConstraint(
            "old_suitability_index >= 0 AND old_suitability_index <= 100",
            name="ck_change_proposals_old_suitability_range",
        ),
        CheckConstraint(
            "new_suitability_index >= 0 AND new_suitability_index <= 100",
            name="ck_change_proposals_new_suitability_range",
        ),
        # Fast lookup of pending proposals for a farm
        Index("ix_change_proposals_farm_id", "farm_id"),
        # Proposals for a specific entry (e.g. "any pending for entry X?")
        Index("ix_change_proposals_entry_id", "entry_id"),
        # Filter pending proposals efficiently
        Index(
            "ix_change_proposals_farm_status",
            "farm_id",
            "status",
        ),
    )
