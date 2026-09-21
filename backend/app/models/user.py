"""
User and installation metadata models.

Requirements: 1.6, 4.1, 11.1, 11.2, 11.4, 11.6
"""

from enum import Enum as PyEnum

from sqlalchemy import CheckConstraint, Enum, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDMixin


class User(Base, UUIDMixin, TimestampMixin):
    """
    User model with external identity mapping.
    
    Requirements: 4.1, 11.1, 11.2, 11.4, 11.6
    
    Users are identified by:
    - Internal UUID primary key (for ownership and performance)
    - Unique (issuer, subject) pair from verified credentials
    
    Email and display name are optional profile fields, not identity keys.
    Multiple users can have the same email from different issuers, or no email.
    
    The unique constraint on (issuer, subject) handles concurrent first-login
    races through database-level uniqueness enforcement.
    """

    __tablename__ = "users"

    # External identity - unique pair from OIDC/demo
    issuer: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)

    # Optional profile fields - not identity keys
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # User preferences as flexible JSONB
    preferences: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # Relationships
    farms: Mapped[list["Farm"]] = relationship(
        "Farm",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    # Unique constraint for identity mapping
    __table_args__ = (
        UniqueConstraint(
            "issuer",
            "subject",
            name="uq_users_issuer_subject",
        ),
        CheckConstraint(
            "issuer != ''",
            name="ck_users_issuer_not_empty",
        ),
        CheckConstraint(
            "subject != ''",
            name="ck_users_subject_not_empty",
        ),
    )


class InstallationMode(str, PyEnum):
    """Installation marker mode"""

    LOCAL_DEMO = "local_demo"
    AUTHENTICATED = "authenticated"


class InstallationMetadata(Base):
    """
    Singleton installation marker.
    
    Requirements: 1.6, 4.3, 11.6, 12.2
    
    This table contains exactly one row identifying whether the database
    is configured for:
    - local_demo: Isolated single-user demo with historical/synthetic fixtures
    - authenticated: Real multi-user deployment with verified credentials
    
    The mode must match the API's auth configuration at startup.
    Mismatches cause startup failure to prevent demo credentials from
    accessing real user data or vice versa.
    
    This table stores NO credentials or personal data - only the operational mode.
    Demo fixtures and the demo user are seeded separately through an explicit
    management command.
    """

    __tablename__ = "installation_metadata"

    # Singleton constraint: only one row allowed
    id: Mapped[int] = mapped_column(
        primary_key=True,
        server_default=text("1"),
    )

    mode: Mapped[InstallationMode] = mapped_column(
        Enum(InstallationMode, native_enum=False, length=20),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "id = 1",
            name="ck_installation_metadata_singleton",
        ),
    )
