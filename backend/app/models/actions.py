"""
ActionCompletion model for persisting farm action completion status.

Requirements: 7.4, 7.5
"""

import uuid

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, CreatedAtMixin, UUIDMixin, UTCTimestamp


class ActionCompletion(Base, UUIDMixin, CreatedAtMixin):
    """
    Records when a farmer marks an action recommendation as complete.

    Requirements: 7.4, 7.5

    Each row captures a (farm, action_rule_id) completion event with a
    timestamp. The unique constraint on (farm_id, action_id) means an action
    can only be completed once per farm — subsequent PATCH calls are idempotent
    upserts that retain the original completion timestamp.

    The runtime role needs SELECT, INSERT, UPDATE (for upsert) on this table.
    """

    __tablename__ = "action_completions"

    farm_id: Mapped[uuid.UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("farms.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Rule identifier from rules_v1.yaml, e.g. "drought-irrigate-or-mulch"
    action_id: Mapped[str] = mapped_column(String(100), nullable=False)

    # When the farmer marked this action as done
    completed_at: Mapped[UTCTimestamp]

    # Relationship back to the farm
    farm: Mapped["Farm"] = relationship("Farm", foreign_keys=[farm_id])

    __table_args__ = (
        # Only one completion record per (farm, action) pair
        UniqueConstraint(
            "farm_id",
            "action_id",
            name="uq_action_completions_farm_action",
        ),
        # Fast lookup of all completed actions for a given farm
        Index("ix_action_completions_farm_id", "farm_id"),
    )
