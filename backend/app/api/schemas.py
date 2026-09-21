"""
Shared API schemas and primitives for FarmTwin backend.

Requirements: 6.1, 7.5, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 12.1, 12.2, 12.3, 12.4
"""

from datetime import date, datetime, timezone
from enum import Enum
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DataMode(str, Enum):
    """
    Data source mode for evidence records.
    Independent of deployment environment.
    Requirements: 12.1
    """

    LIVE = "live"
    HISTORICAL_REPLAY = "historical_replay"
    DEMONSTRATION = "demonstration"


class AuthMode(str, Enum):
    """
    Authentication mode for operational context.
    Requirements: 12.2
    """

    OIDC = "oidc"
    LOCAL_DEMO = "local_demo"


# UTC timestamp type
UTCDatetime = Annotated[
    datetime,
    Field(
        description="UTC timestamp with timezone information. "
        "All timestamps are normalized to UTC."
    ),
]


# Date-only type (for domain values like planting dates)
DateOnly = Annotated[
    date,
    Field(description="Date-only value without time information"),
]


class BaseSchema(BaseModel):
    """
    Base schema for all API models with shared configuration.
    
    - Rejects unexpected fields on write schemas (extra='forbid')
    - Rejects NaN and infinity in numeric fields (allow_inf_nan=False)
    - Requirements: 8.1, 8.4
    """

    model_config = ConfigDict(
        # Reject unexpected fields in write operations
        extra="forbid",
        # Validate on assignment
        validate_assignment=True,
        # Use enum values in JSON
        use_enum_values=False,
        # Don't allow population by field name
        populate_by_name=True,
    )

    # Global field configuration for numeric validation
    @field_validator("*", mode="before")
    @classmethod
    def reject_non_finite_numbers(cls, v: Any, info) -> Any:
        """
        Reject NaN and infinity in all numeric fields.
        Requirements: 8.4
        """
        if isinstance(v, float):
            import math

            if math.isnan(v):
                raise ValueError(f"{info.field_name}: NaN is not allowed")
            if math.isinf(v):
                raise ValueError(
                    f"{info.field_name}: Infinity is not allowed"
                )
        return v


class ReadBaseSchema(BaseModel):
    """
    Base schema for read operations (responses).
    Allows unknown fields for forward compatibility.
    Requirements: 8.1
    """

    model_config = ConfigDict(
        # Allow unknown fields in responses
        extra="ignore",
        use_enum_values=False,
        populate_by_name=True,
    )


class TimestampMixin(BaseModel):
    """
    Mixin for UTC timestamp fields in responses.
    Requirements: 7.5, 8.5
    """

    created_at: UTCDatetime = Field(
        description="Creation timestamp in UTC"
    )
    updated_at: UTCDatetime | None = Field(
        None, description="Last update timestamp in UTC"
    )

    @field_validator("created_at", "updated_at", mode="after")
    @classmethod
    def normalize_to_utc(cls, v: Any) -> datetime | None:
        """
        Require timezone-aware timestamps and normalize to UTC.
        Requirements: 7.5, 8.5
        """
        if v is None:
            return None

        if isinstance(v, datetime):
            if v.tzinfo is None:
                raise ValueError(
                    "Timezone-aware timestamp required. "
                    "Naive datetime not allowed."
                )
            # Normalize to UTC
            return v.astimezone(timezone.utc)

        raise ValueError("Timestamp must be a datetime")


class PaginationParams(BaseModel):
    """
    Pagination query parameters with validation.
    Requirements: 7.5, 8.2, 8.3
    """

    limit: Annotated[int, Field(ge=1, le=100)] = Field(
        default=50, description="Number of items to return (1-100)"
    )
    offset: Annotated[int, Field(ge=0)] = Field(
        default=0, description="Number of items to skip"
    )

    model_config = ConfigDict(extra="forbid")


class PaginatedResponse(ReadBaseSchema):
    """
    Standard paginated response wrapper.
    Requirements: 7.5, 8.6
    """

    items: list[Any] = Field(description="Items in this page")
    limit: Annotated[int, Field(ge=1, le=100)] = Field(
        description="Requested page size"
    )
    offset: Annotated[int, Field(ge=0)] = Field(
        description="Offset into total results"
    )
    total: Annotated[int, Field(ge=0)] = Field(
        description="Total number of items matching the query (scoped to user)"
    )


class ErrorDetail(BaseModel):
    """
    Field-level error detail.
    Requirements: 6.1
    """

    field: str = Field(description="Field path (e.g., 'query.limit')")
    code: str = Field(description="Stable error code")
    message: str = Field(description="Human-readable error message")

    model_config = ConfigDict(extra="forbid")


class ErrorResponse(BaseModel):
    """
    Standard error response envelope.
    Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7
    """

    error: "ErrorEnvelope" = Field(description="Error information")

    model_config = ConfigDict(extra="forbid")


class ErrorEnvelope(BaseModel):
    """
    Error information envelope.
    Requirements: 6.1, 6.6, 6.7
    """

    code: str = Field(description="Stable error code")
    message: str = Field(description="Error message")
    request_id: UUID = Field(description="Server-generated request ID")
    details: list[ErrorDetail] = Field(
        default_factory=list,
        description="Field-level error details (empty when not applicable)",
    )

    model_config = ConfigDict(extra="forbid")


class OperationalModeInfo(ReadBaseSchema):
    """
    Operational mode information included in responses.
    Requirements: 12.2
    """

    data_mode: DataMode = Field(
        description="Configured data mode for this deployment"
    )
    auth_mode: AuthMode = Field(
        description="Configured authentication mode"
    )
    non_live: bool = Field(
        default=False,
        description="True if this is a non-live demonstration environment",
    )


class EvidenceQuality(str, Enum):
    """
    Quality indicator for evidence records.
    Requirements: 12.3
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNCERTAIN = "uncertain"


class MissingReason(str, Enum):
    """
    Reason why an evidence value is missing.
    Requirements: 12.3
    """

    NOT_AVAILABLE = "not_available"
    NOT_APPLICABLE = "not_applicable"
    PROVIDER_ERROR = "provider_error"
    INSUFFICIENT_QUALITY = "insufficient_quality"


class EvidenceMetadata(ReadBaseSchema):
    """
    Future evidence metadata for observations and measurements.
    Defines the structure without implementing provider integration.
    Requirements: 12.3, 12.4
    """

    source_id: str = Field(
        description="Provider or source identifier"
    )
    valid_time: UTCDatetime = Field(
        description="Time or start of interval for which this evidence is valid"
    )
    valid_time_end: UTCDatetime | None = Field(
        None,
        description="End of validity interval (null for instantaneous observations)",
    )
    acquisition_time: UTCDatetime | None = Field(
        None,
        description="When this evidence was acquired or issued by the provider",
    )
    quality: EvidenceQuality | None = Field(
        None, description="Quality indicator"
    )
    data_mode: DataMode = Field(
        description="Source-specific data mode (preserved when mixed)"
    )
    missing_reason: MissingReason | None = Field(
        None,
        description="Reason if value is null (null if value is present)",
    )

    @field_validator("valid_time", "valid_time_end", "acquisition_time", mode="after")
    @classmethod
    def normalize_evidence_times_to_utc(cls, v: Any) -> datetime | None:
        """
        Require timezone-aware timestamps and normalize to UTC.
        Requirements: 7.5, 8.5, 12.3
        """
        if v is None:
            return None

        if isinstance(v, datetime):
            if v.tzinfo is None:
                raise ValueError(
                    "Evidence timestamps must be timezone-aware. "
                    "Naive datetime not allowed."
                )
            return v.astimezone(timezone.utc)

        raise ValueError("Evidence timestamp must be a datetime")


def persisted_datetime_to_utc(value: datetime | None) -> datetime | None:
    """Attach UTC to database UTC-naive values and normalize aware values."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class DecisionReference(ReadBaseSchema):
    """
    Future decision reference metadata.
    Includes snapshot ID, geometry revision, and engine version.
    Requirements: 12.4
    """

    snapshot_id: UUID | None = Field(
        None, description="Snapshot identifier for this decision"
    )
    geometry_revision: int | None = Field(
        None,
        description="Farm geometry revision number used in this decision",
    )
    engine_version: str | None = Field(
        None, description="Decision engine version identifier"
    )


# String validation constraints for common domain fields
class StringConstraints:
    """
    Validation constraints for string fields.
    Requirements: 8.6
    """

    # Non-blank name (1-255 characters, trimmed)
    NAME_MIN_LENGTH = 1
    NAME_MAX_LENGTH = 255

    @staticmethod
    def validate_name(v: str | None, field_name: str = "name") -> str:
        """
        Validate a name field: non-blank, trimmed, within length limits.
        Requirements: 8.6
        """
        if v is None:
            raise ValueError(f"{field_name} is required")

        trimmed = v.strip()
        if not trimmed:
            raise ValueError(f"{field_name} cannot be blank")

        if len(trimmed) < StringConstraints.NAME_MIN_LENGTH:
            raise ValueError(
                f"{field_name} must be at least "
                f"{StringConstraints.NAME_MIN_LENGTH} character(s)"
            )

        if len(trimmed) > StringConstraints.NAME_MAX_LENGTH:
            raise ValueError(
                f"{field_name} must be at most "
                f"{StringConstraints.NAME_MAX_LENGTH} characters"
            )

        return trimmed
