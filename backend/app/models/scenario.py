"""
Saved scenario model.

Stores immutable named scenario computations derived from a baseline
AnalysisSnapshot with applied climate deltas.

Requirements: 2.5, 2.7
"""

import uuid

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, CreatedAtMixin, UUIDMixin


class SavedScenario(Base, UUIDMixin, CreatedAtMixin):
    """
    Immutable saved scenario record.

    Each record captures:
    - The farm it belongs to
    - The baseline snapshot it was derived from
    - The delta parameters applied (rainfall_change_pct, temperature_change_c,
      irrigation_mm_override)
    - The computed results (baseline + scenario crop/hazard values)
    - The engine version used for reproducibility

    Immutability is enforced at the application level; once created, a
    SavedScenario is never overwritten (Requirement 2.7).

    Requirements: 2.5, 2.7
    """

    __tablename__ = "saved_scenarios"

    farm_id: Mapped[uuid.UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("farms.id", ondelete="CASCADE"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    baseline_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("analysis_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Delta parameters applied to the baseline
    delta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Computed results: {crops: [...], hazards: [...]}
    results: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Engine version string for reproducibility
    engine_version: Mapped[str] = mapped_column(String(100), nullable=False)

    # Relationships
    farm: Mapped["Farm"] = relationship("Farm", foreign_keys=[farm_id])
    baseline_snapshot: Mapped["AnalysisSnapshot | None"] = relationship(
        "AnalysisSnapshot",
        foreign_keys=[baseline_snapshot_id],
    )

    __table_args__ = (
        Index("ix_saved_scenarios_farm_id", "farm_id"),
        Index("ix_saved_scenarios_farm_created", "farm_id", "created_at"),
    )
