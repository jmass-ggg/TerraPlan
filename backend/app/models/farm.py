"""
Farm and geometry revision models.

Requirements: 2.4, 2.5, 11.1, 11.2, 11.4, 11.5, 11.6, 11.7
"""

import uuid

from geoalchemy2 import Geometry
from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, CreatedAtMixin, TimestampMixin, UUIDMixin


class Farm(Base, UUIDMixin, TimestampMixin):
    """
    Farm model with user ownership and geometry revision tracking.
    
    Requirements: 2.4, 11.1, 11.2, 11.4, 11.5, 11.6, 11.7
    
    Each farm:
    - Belongs to exactly one user (non-null ownership)
    - Has a non-blank name
    - References its current geometry revision
    - Tracks creation and modification timestamps
    
    The current_geometry_revision is a positive integer that must reference
    an actual revision belonging to this farm. This constraint is enforced
    through a composite foreign key that's deferred to allow atomic insertion
    of a farm and its first revision.
    
    Deletion behavior:
    - User deletion: RESTRICT (cannot delete user with farms)
    - Farm deletion: Retained for Phase 4 (explicit confirmed deletion)
    
    Full geometry validation and geodesic computation belong to Phase 4.
    """

    __tablename__ = "farms"

    # Ownership - non-null user reference
    user_id: Mapped[uuid.UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Farm metadata
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # A retry key is unique per owner when present. Different users may use
    # the same client-generated UUID without sharing idempotency state.
    idempotency_key: Mapped[uuid.UUID | None] = mapped_column(
        PostgresUUID(as_uuid=True),
        nullable=True,
    )

    # Current geometry revision - positive integer
    current_geometry_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="farms")
    geometry_revisions: Mapped[list["FarmGeometryRevision"]] = relationship(
        "FarmGeometryRevision",
        back_populates="farm",
        foreign_keys="FarmGeometryRevision.farm_id",
        cascade="all, delete-orphan",
    )
    current_geometry: Mapped["FarmGeometryRevision"] = relationship(
        "FarmGeometryRevision",
        foreign_keys="[Farm.id, Farm.current_geometry_revision]",
        viewonly=True,
    )

    __table_args__ = (
        # Non-blank name constraint
        CheckConstraint(
            "name != '' AND trim(name) = name",
            name="ck_farms_name_not_blank",
        ),
        # Positive revision constraint
        CheckConstraint(
            "current_geometry_revision > 0",
            name="ck_farms_current_revision_positive",
        ),
        # Composite foreign key to ensure current revision belongs to this farm
        # Deferred to allow atomic insertion of farm + first revision
        ForeignKeyConstraint(
            ["id", "current_geometry_revision"],
            ["farm_geometry_revisions.farm_id", "farm_geometry_revisions.revision"],
            name="fk_farms_current_geometry",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        # Index for ownership queries
        Index("ix_farms_user_created", "user_id", "created_at", "id"),
        Index(
            "uq_farms_user_idempotency_key",
            "user_id",
            "idempotency_key",
            unique=True,
            postgresql_where=idempotency_key.is_not(None),
        ),
    )


class FarmGeometryRevision(Base, UUIDMixin, CreatedAtMixin):
    """
    Immutable geometry revision for a farm.
    
    Requirements: 2.4, 2.5, 11.1, 11.2, 11.5, 11.6, 11.7
    
    Each revision stores:
    - WGS84 (SRID 4326) polygon geometry
    - Centroid point
    - Interior label point (for labels/markers)
    - Area in hectares (finite positive value)
    - Sequential revision number per farm
    
    Revisions are immutable - boundary edits create new revisions.
    The runtime database role cannot UPDATE these rows.
    
    Constraints:
    - Unique (farm_id, revision) per farm
    - Non-null geometry fields
    - Finite positive hectares
    - SRID 4326 enforced by GeoAlchemy2
    
    Indexes:
    - GiST on polygon geometry for spatial queries
    - B-tree on (farm_id, revision) for revision lookups
    
    Full topology validation and geodesic area computation are Phase 4
    responsibilities. These constraints prevent obviously invalid data
    but don't prove correctness.
    """

    __tablename__ = "farm_geometry_revisions"

    # Farm reference - non-null ownership
    farm_id: Mapped[uuid.UUID] = mapped_column(
        PostgresUUID(as_uuid=True),
        ForeignKey("farms.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Sequential revision number per farm (positive integer)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)

    # WGS84/SRID 4326 geometry fields
    # Using GeoAlchemy2 Geometry type with explicit SRID
    geometry: Mapped[bytes] = mapped_column(
        Geometry(geometry_type="POLYGON", srid=4326),
        nullable=False,
    )

    centroid: Mapped[bytes] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326),
        nullable=False,
    )

    label_point: Mapped[bytes] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326),
        nullable=False,
    )

    # Area in hectares - finite positive value
    hectares: Mapped[float] = mapped_column(Float, nullable=False)

    # Relationship back to farm
    farm: Mapped["Farm"] = relationship(
        "Farm",
        back_populates="geometry_revisions",
        foreign_keys=[farm_id],
    )

    __table_args__ = (
        # Unique (farm_id, revision) constraint
        UniqueConstraint(
            "farm_id",
            "revision",
            name="uq_farm_geometry_revisions_farm_revision",
        ),
        # Positive revision constraint
        CheckConstraint(
            "revision > 0",
            name="ck_farm_geometry_revisions_revision_positive",
        ),
        # Finite positive hectares constraint
        CheckConstraint(
            "hectares > 0 AND hectares = hectares",  # NaN check: NaN != NaN
            name="ck_farm_geometry_revisions_hectares_valid",
        ),
        # GiST index for spatial queries
        Index(
            "ix_farm_geometry_revisions_geometry",
            "geometry",
            postgresql_using="gist",
        ),
        # B-tree index for farm/revision lookups
        Index(
            "ix_farm_geometry_revisions_farm_revision",
            "farm_id",
            "revision",
        ),
    )
