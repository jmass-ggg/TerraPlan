"""
SQLAlchemy base and mixins for FarmTwin models.

Requirements: 11.1, 11.2, 11.3
"""

import uuid
from datetime import datetime, timezone
from typing import Annotated

from sqlalchemy import DateTime, MetaData, text
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# Use naming convention for constraints to ensure stable migration behavior
convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


metadata_obj = MetaData(naming_convention=convention)


class Base(DeclarativeBase):
    """
    SQLAlchemy declarative base for all models.
    
    Requirements: 11.1
    
    All models inherit from this base and use:
    - UUID primary keys for users and user-created records
    - Named constraints for stable diagnostics
    - Explicit type annotations
    """

    metadata = metadata_obj

    # Type annotation for UUID primary keys
    type_annotation_map = {
        uuid.UUID: PostgresUUID(as_uuid=True),
    }


# Type alias for UUID primary key columns
# Uses database-generated UUID defaults
UUIDPrimaryKey = Annotated[
    uuid.UUID,
    mapped_column(
        PostgresUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    ),
]


# Type alias for UTC timestamp with database default
UTCTimestamp = Annotated[
    datetime,
    mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    ),
]


class UUIDMixin:
    """
    Mixin providing UUID primary key.
    
    Requirements: 11.1
    
    Uses database-generated UUIDs via gen_random_uuid() for security
    and to avoid client-side generation issues.
    """

    id: Mapped[UUIDPrimaryKey]


class TimestampMixin:
    """
    Mixin providing creation and update timestamps.
    
    Requirements: 11.2, 11.3
    
    Timestamps are:
    - Timezone-aware UTC values
    - Set by database defaults (not application code)
    - Updated via database trigger for mutable records
    
    The update trigger must be installed via migration for tables
    that use this mixin and are mutable.
    """

    created_at: Mapped[UTCTimestamp]
    updated_at: Mapped[UTCTimestamp]


class CreatedAtMixin:
    """
    Mixin providing only creation timestamp for immutable records.
    
    Requirements: 11.2
    
    Used for records that never change after creation
    (e.g., geometry revisions).
    """

    created_at: Mapped[UTCTimestamp]


class OwnershipMixin:
    """
    Mixin providing user ownership reference.
    
    Requirements: 11.4
    
    Models using this mixin must define the foreign key relationship
    in their own class to specify deletion behavior and constraints.
    This mixin only provides the column type annotation.
    """

    user_id: Mapped[uuid.UUID]
