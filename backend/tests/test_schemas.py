"""
Tests for shared API schemas and validation.

Feature: backend-foundation
Property 5: Typed validation preserves boundaries
Property 10: Honest data modes

Requirements: 7.5, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 12.1, 12.2, 12.3, 12.4, 12.5
"""

import math
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from hypothesis import given, strategies as st
from pydantic import ValidationError

from app.api.schemas import (
    BaseSchema,
    DataMode,
    DecisionReference,
    ErrorDetail,
    ErrorEnvelope,
    ErrorResponse,
    EvidenceMetadata,
    EvidenceQuality,
    MissingReason,
    OperationalModeInfo,
    PaginatedResponse,
    PaginationParams,
    ReadBaseSchema,
    StringConstraints,
    TimestampMixin,
)


class TestPaginationValidation:
    """
    Test pagination parameter validation.
    Requirements: 8.2, 8.3
    """

    def test_default_pagination(self):
        """Test default pagination values"""
        params = PaginationParams()
        assert params.limit == 50
        assert params.offset == 0

    def test_valid_pagination(self):
        """Test valid pagination ranges"""
        params = PaginationParams(limit=100, offset=0)
        assert params.limit == 100
        assert params.offset == 0

        params = PaginationParams(limit=1, offset=0)
        assert params.limit == 1

    def test_limit_too_low(self):
        """Test limit below minimum"""
        with pytest.raises(ValidationError) as exc_info:
            PaginationParams(limit=0)
        assert "limit" in str(exc_info.value).lower()

    def test_limit_too_high(self):
        """Test limit above maximum"""
        with pytest.raises(ValidationError) as exc_info:
            PaginationParams(limit=101)
        assert "limit" in str(exc_info.value).lower()

    def test_negative_offset(self):
        """Test negative offset"""
        with pytest.raises(ValidationError) as exc_info:
            PaginationParams(offset=-1)
        assert "offset" in str(exc_info.value).lower()

    def test_unexpected_fields_rejected(self):
        """Test that unexpected fields are rejected (Req 8.1)"""
        with pytest.raises(ValidationError) as exc_info:
            PaginationParams(limit=50, offset=0, extra_field="unexpected")
        assert "extra_field" in str(exc_info.value).lower() or "extra" in str(
            exc_info.value
        ).lower()


class TestNonFiniteNumberValidation:
    """
    Test rejection of NaN and infinity in numeric fields.
    Requirements: 8.4
    """

    class NumericSchema(BaseSchema):
        """Test schema with numeric field"""

        value: float
        count: int = 0

    def test_valid_number(self):
        """Test valid numeric values"""
        schema = self.NumericSchema(value=42.5)
        assert schema.value == 42.5

        schema = self.NumericSchema(value=0.0)
        assert schema.value == 0.0

        schema = self.NumericSchema(value=-123.456)
        assert schema.value == -123.456

    def test_nan_rejected(self):
        """Test that NaN is rejected"""
        with pytest.raises(ValidationError) as exc_info:
            self.NumericSchema(value=float("nan"))
        error_msg = str(exc_info.value).lower()
        assert "nan" in error_msg

    def test_positive_infinity_rejected(self):
        """Test that positive infinity is rejected"""
        with pytest.raises(ValidationError) as exc_info:
            self.NumericSchema(value=float("inf"))
        error_msg = str(exc_info.value).lower()
        assert "infinity" in error_msg or "inf" in error_msg

    def test_negative_infinity_rejected(self):
        """Test that negative infinity is rejected"""
        with pytest.raises(ValidationError) as exc_info:
            self.NumericSchema(value=float("-inf"))
        error_msg = str(exc_info.value).lower()
        assert "infinity" in error_msg or "inf" in error_msg


class TestTimestampValidation:
    """
    Test UTC timestamp normalization and validation.
    Requirements: 7.5, 8.5
    """

    class TimestampSchema(TimestampMixin):
        """Test schema with timestamps"""

        pass

    def test_utc_timestamp_preserved(self):
        """Test UTC timestamp is preserved"""
        now = datetime.now(timezone.utc)
        schema = self.TimestampSchema(created_at=now)
        assert schema.created_at == now
        assert schema.created_at.tzinfo == timezone.utc

    def test_other_timezone_normalized_to_utc(self):
        """Test non-UTC timezone is normalized to UTC"""
        import zoneinfo

        # Create timestamp in different timezone
        eastern = zoneinfo.ZoneInfo("America/New_York")
        dt_eastern = datetime(2025, 6, 1, 12, 0, 0, tzinfo=eastern)

        schema = self.TimestampSchema(created_at=dt_eastern)

        # Should be normalized to UTC
        assert schema.created_at.tzinfo == timezone.utc
        # Value should represent same instant
        assert schema.created_at.timestamp() == dt_eastern.timestamp()

    def test_naive_datetime_rejected(self):
        """Test that naive datetime without timezone is rejected"""
        naive_dt = datetime(2025, 6, 1, 12, 0, 0)  # No timezone

        with pytest.raises(ValidationError) as exc_info:
            self.TimestampSchema(created_at=naive_dt)
        error_msg = str(exc_info.value).lower()
        assert "timezone" in error_msg or "aware" in error_msg

    def test_optional_updated_at(self):
        """Test optional updated_at field"""
        now = datetime.now(timezone.utc)
        schema = self.TimestampSchema(created_at=now)
        assert schema.updated_at is None

        schema = self.TimestampSchema(created_at=now, updated_at=now)
        assert schema.updated_at == now


class TestStringValidation:
    """
    Test string validation constraints.
    Requirements: 8.6
    """

    def test_valid_name(self):
        """Test valid name strings"""
        assert StringConstraints.validate_name("Farm 1") == "Farm 1"
        assert StringConstraints.validate_name("A") == "A"
        assert StringConstraints.validate_name("  Trimmed  ") == "Trimmed"

    def test_blank_name_rejected(self):
        """Test blank names are rejected"""
        with pytest.raises(ValueError) as exc_info:
            StringConstraints.validate_name("")
        assert "blank" in str(exc_info.value).lower()

        with pytest.raises(ValueError) as exc_info:
            StringConstraints.validate_name("   ")
        assert "blank" in str(exc_info.value).lower()

    def test_none_name_rejected(self):
        """Test None name is rejected"""
        with pytest.raises(ValueError) as exc_info:
            StringConstraints.validate_name(None)
        assert "required" in str(exc_info.value).lower()

    def test_oversized_name_rejected(self):
        """Test oversized names are rejected"""
        long_name = "X" * 256
        with pytest.raises(ValueError) as exc_info:
            StringConstraints.validate_name(long_name)
        assert "255" in str(exc_info.value)


class TestErrorResponse:
    """
    Test error response schema.
    Requirements: 6.1
    """

    def test_error_response_structure(self):
        """Test error response envelope"""
        request_id = uuid4()
        error_env = ErrorEnvelope(
            code="VALIDATION_ERROR",
            message="Invalid request",
            request_id=request_id,
            details=[],
        )
        response = ErrorResponse(error=error_env)

        assert response.error.code == "VALIDATION_ERROR"
        assert response.error.message == "Invalid request"
        assert response.error.request_id == request_id
        assert response.error.details == []

    def test_error_with_details(self):
        """Test error with field details"""
        detail = ErrorDetail(
            field="query.limit",
            code="OUT_OF_RANGE",
            message="Value must be between 1 and 100",
        )
        error_env = ErrorEnvelope(
            code="VALIDATION_ERROR",
            message="Invalid request",
            request_id=uuid4(),
            details=[detail],
        )

        assert len(error_env.details) == 1
        assert error_env.details[0].field == "query.limit"


class TestEvidenceMetadata:
    """
    Test evidence metadata schemas.
    Requirements: 12.3, 12.4
    """

    def test_evidence_with_value(self):
        """Test evidence record with value"""
        now = datetime.now(timezone.utc)
        evidence = EvidenceMetadata(
            source_id="weather_api",
            valid_time=now,
            data_mode=DataMode.LIVE,
            quality=EvidenceQuality.HIGH,
        )

        assert evidence.source_id == "weather_api"
        assert evidence.valid_time == now
        assert evidence.data_mode == DataMode.LIVE
        assert evidence.quality == EvidenceQuality.HIGH
        assert evidence.missing_reason is None

    def test_evidence_with_missing_value(self):
        """Test evidence record with missing value"""
        now = datetime.now(timezone.utc)
        evidence = EvidenceMetadata(
            source_id="weather_api",
            valid_time=now,
            data_mode=DataMode.LIVE,
            quality=EvidenceQuality.LOW,
            missing_reason=MissingReason.PROVIDER_ERROR,
        )

        assert evidence.missing_reason == MissingReason.PROVIDER_ERROR

    def test_evidence_time_normalization(self):
        """Test evidence timestamps are normalized to UTC"""
        import zoneinfo

        eastern = zoneinfo.ZoneInfo("America/New_York")
        dt_eastern = datetime(2025, 6, 1, 12, 0, 0, tzinfo=eastern)

        evidence = EvidenceMetadata(
            source_id="test",
            valid_time=dt_eastern,
            data_mode=DataMode.HISTORICAL_REPLAY,
        )

        assert evidence.valid_time.tzinfo == timezone.utc

    def test_evidence_naive_time_rejected(self):
        """Test naive datetime rejected in evidence"""
        naive_dt = datetime(2025, 6, 1, 12, 0, 0)

        with pytest.raises(ValidationError) as exc_info:
            EvidenceMetadata(
                source_id="test",
                valid_time=naive_dt,
                data_mode=DataMode.LIVE,
            )
        assert "timezone" in str(exc_info.value).lower() or "aware" in str(
            exc_info.value
        ).lower()


class TestDecisionReference:
    """
    Test decision reference metadata.
    Requirements: 12.4
    """

    def test_decision_reference_with_all_fields(self):
        """Test decision reference with all fields"""
        snapshot_id = uuid4()
        decision = DecisionReference(
            snapshot_id=snapshot_id,
            geometry_revision=5,
            engine_version="v1.2.3",
        )

        assert decision.snapshot_id == snapshot_id
        assert decision.geometry_revision == 5
        assert decision.engine_version == "v1.2.3"

    def test_decision_reference_with_nulls(self):
        """Test decision reference preserves unknowns as null"""
        decision = DecisionReference(
            snapshot_id=None,
            geometry_revision=None,
            engine_version=None,
        )

        assert decision.snapshot_id is None
        assert decision.geometry_revision is None
        assert decision.engine_version is None


# Property-Based Tests
# Feature: backend-foundation, Property 5: Typed validation preserves boundaries


@given(
    limit=st.integers(min_value=-100, max_value=200),
    offset=st.integers(min_value=-100, max_value=1000),
)
def test_property_pagination_boundaries(limit: int, offset: int):
    """
    Property 5: For any limit/offset, validation rejects out-of-range values.
    
    **Validates: Requirements 8.2, 8.3**
    """
    if 1 <= limit <= 100 and offset >= 0:
        # Should succeed
        params = PaginationParams(limit=limit, offset=offset)
        assert params.limit == limit
        assert params.offset == offset
    else:
        # Should fail
        with pytest.raises(ValidationError):
            PaginationParams(limit=limit, offset=offset)


@given(
    value=st.one_of(
        st.floats(min_value=-1e10, max_value=1e10),
        st.just(float("nan")),
        st.just(float("inf")),
        st.just(float("-inf")),
    )
)
def test_property_non_finite_rejection(value: float):
    """
    Property 5: For any float value, NaN and infinity are rejected.
    
    **Validates: Requirements 8.4**
    """

    class TestSchema(BaseSchema):
        num: float

    if math.isnan(value) or math.isinf(value):
        # Should reject
        with pytest.raises(ValidationError) as exc_info:
            TestSchema(num=value)
        error_msg = str(exc_info.value).lower()
        assert "nan" in error_msg or "infinity" in error_msg or "inf" in error_msg
    else:
        # Should accept
        schema = TestSchema(num=value)
        assert schema.num == value


@given(
    dt=st.datetimes(
        min_value=datetime(2020, 1, 1),
        max_value=datetime(2030, 12, 31),
    )
)
def test_property_naive_datetime_rejection(dt: datetime):
    """
    Property 5: For any datetime, naive datetimes (no timezone) are rejected.
    
    **Validates: Requirements 7.5, 8.5**
    """

    class TestSchema(TimestampMixin):
        pass

    if dt.tzinfo is None:
        # Should reject naive
        with pytest.raises(ValidationError) as exc_info:
            TestSchema(created_at=dt)
        error_msg = str(exc_info.value).lower()
        assert "timezone" in error_msg or "aware" in error_msg
    else:
        # Should accept and normalize to UTC
        schema = TestSchema(created_at=dt)
        assert schema.created_at.tzinfo == timezone.utc


@given(name=st.text(min_size=0, max_size=300))
def test_property_string_validation(name: str):
    """
    Property 5: For any string, blank and oversized names are rejected.
    
    **Validates: Requirements 8.6**
    """
    trimmed = name.strip()

    if len(trimmed) == 0:
        # Blank - should reject
        with pytest.raises(ValueError) as exc_info:
            StringConstraints.validate_name(name)
        assert "blank" in str(exc_info.value).lower()
    elif len(trimmed) > 255:
        # Oversized - should reject
        with pytest.raises(ValueError) as exc_info:
            StringConstraints.validate_name(name)
        assert "255" in str(exc_info.value)
    else:
        # Valid - should accept and trim
        result = StringConstraints.validate_name(name)
        assert result == trimmed
        assert 1 <= len(result) <= 255
