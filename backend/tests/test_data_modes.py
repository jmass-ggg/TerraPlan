"""
Tests for honest data-mode serialization.

Feature: backend-foundation
Property 10: Honest data modes

Requirements: 12.1, 12.2, 12.3, 12.4, 12.5
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from hypothesis import given, strategies as st

from app.api.schemas import DataMode
from app.core.config import AuthMode, DataMode as ConfigDataMode, Environment, Settings
from app.core.response import (
    DecisionMetadataBuilder,
    HonestEvidenceSerializer,
    get_operational_mode_headers,
    is_non_live_mode,
)


class TestOperationalModeHeaders:
    """
    Test operational mode header generation.
    Requirements: 12.2
    """

    def test_live_mode_headers(self):
        """Test headers for live operational mode"""
        # Use env var style for nested settings with delimiter
        import os
        
        os.environ.update({
            "ENVIRONMENT": "production",
            "DATA_MODE": "live",
            "AUTH__MODE": "oidc",
            "AUTH__ISSUER": "https://auth.example.com",
            "AUTH__AUDIENCE": "farmtwin-api",
            "AUTH__JWKS_URL": "https://auth.example.com/.well-known/jwks.json",
            "AUTH__ALGORITHMS": '["RS256"]',
        })
        
        try:
            settings = Settings()
            headers = get_operational_mode_headers(settings)

            assert headers["X-FarmTwin-Data-Mode"] == "live"
            assert headers["X-FarmTwin-Auth-Mode"] == "oidc"
        finally:
            # Clean up env vars
            for key in ["ENVIRONMENT", "DATA_MODE", "AUTH__MODE", "AUTH__ISSUER", 
                       "AUTH__AUDIENCE", "AUTH__JWKS_URL", "AUTH__ALGORITHMS"]:
                os.environ.pop(key, None)

    def test_historical_mode_headers(self):
        """Test headers for historical replay mode"""
        import os
        
        os.environ.update({
            "ENVIRONMENT": "staging",
            "DATA_MODE": "historical_replay",
            "AUTH__MODE": "oidc",
            "AUTH__ISSUER": "https://auth.example.com",
            "AUTH__AUDIENCE": "farmtwin-api",
            "AUTH__JWKS_URL": "https://auth.example.com/.well-known/jwks.json",
            "AUTH__ALGORITHMS": '["RS256"]',
        })
        
        try:
            settings = Settings()
            headers = get_operational_mode_headers(settings)

            assert headers["X-FarmTwin-Data-Mode"] == "historical_replay"
            assert headers["X-FarmTwin-Auth-Mode"] == "oidc"
        finally:
            for key in ["ENVIRONMENT", "DATA_MODE", "AUTH__MODE", "AUTH__ISSUER", 
                       "AUTH__AUDIENCE", "AUTH__JWKS_URL", "AUTH__ALGORITHMS"]:
                os.environ.pop(key, None)

    def test_demo_mode_headers(self):
        """Test headers for demonstration mode"""
        import os
        
        os.environ.update({
            "ENVIRONMENT": "development",
            "DATA_MODE": "demonstration",
            "AUTH__MODE": "local_demo",
            "DEMO__LOCAL_ONLY": "true",
            "DEMO__ISOLATED_DATABASE": "true",
        })
        
        try:
            settings = Settings()
            headers = get_operational_mode_headers(settings)

            assert headers["X-FarmTwin-Data-Mode"] == "demonstration"
            assert headers["X-FarmTwin-Auth-Mode"] == "local_demo"
        finally:
            for key in ["ENVIRONMENT", "DATA_MODE", "AUTH__MODE", "DEMO__LOCAL_ONLY",
                       "DEMO__ISOLATED_DATABASE"]:
                os.environ.pop(key, None)

    def test_is_non_live_mode(self):
        """Test detection of non-live modes"""
        import os
        
        # Test live mode
        os.environ.update({
            "ENVIRONMENT": "production",
            "DATA_MODE": "live",
            "AUTH__MODE": "oidc",
            "AUTH__ISSUER": "https://auth.example.com",
            "AUTH__AUDIENCE": "farmtwin-api",
            "AUTH__JWKS_URL": "https://auth.example.com/.well-known/jwks.json",
            "AUTH__ALGORITHMS": '["RS256"]',
        })
        
        try:
            live_settings = Settings()
            assert not is_non_live_mode(live_settings)
        finally:
            for key in ["ENVIRONMENT", "DATA_MODE", "AUTH__MODE", "AUTH__ISSUER", 
                       "AUTH__AUDIENCE", "AUTH__JWKS_URL", "AUTH__ALGORITHMS"]:
                os.environ.pop(key, None)

        # Test historical mode
        os.environ.update({
            "ENVIRONMENT": "staging",
            "DATA_MODE": "historical_replay",
            "AUTH__MODE": "oidc",
            "AUTH__ISSUER": "https://auth.example.com",
            "AUTH__AUDIENCE": "farmtwin-api",
            "AUTH__JWKS_URL": "https://auth.example.com/.well-known/jwks.json",
            "AUTH__ALGORITHMS": '["RS256"]',
        })
        
        try:
            historical_settings = Settings()
            assert is_non_live_mode(historical_settings)
        finally:
            for key in ["ENVIRONMENT", "DATA_MODE", "AUTH__MODE", "AUTH__ISSUER", 
                       "AUTH__AUDIENCE", "AUTH__JWKS_URL", "AUTH__ALGORITHMS"]:
                os.environ.pop(key, None)

        # Test demo mode
        os.environ.update({
            "ENVIRONMENT": "development",
            "DATA_MODE": "demonstration",
            "AUTH__MODE": "local_demo",
            "DEMO__LOCAL_ONLY": "true",
            "DEMO__ISOLATED_DATABASE": "true",
        })
        
        try:
            demo_settings = Settings()
            assert is_non_live_mode(demo_settings)
        finally:
            for key in ["ENVIRONMENT", "DATA_MODE", "AUTH__MODE", "DEMO__LOCAL_ONLY",
                       "DEMO__ISOLATED_DATABASE"]:
                os.environ.pop(key, None)


class TestHonestEvidenceSerialization:
    """
    Test that evidence preserves source-level data modes.
    Requirements: 12.3, 12.4
    """

    def test_preserve_historical_mode_in_live_runtime(self):
        """
        Test historical records retain their mode even in live runtime.
        Requirements: 12.3
        """
        # Historical fixture record
        historical_record = {
            "source_id": "weather_fixture",
            "valid_time": "2025-06-01T12:00:00Z",
            "data_mode": "historical_replay",
            "value": 25.5,
        }

        # Runtime is live, but record should stay historical
        result = HonestEvidenceSerializer.preserve_source_mode(
            [historical_record], runtime_mode="live"
        )

        assert len(result) == 1
        # Source mode must be preserved, not overwritten
        assert result[0]["data_mode"] == "historical_replay"

    def test_preserve_demo_mode_in_historical_runtime(self):
        """
        Test demo records retain their mode under different runtime.
        Requirements: 12.3
        """
        demo_record = {
            "source_id": "synthetic_data",
            "valid_time": "2025-06-15T10:00:00Z",
            "data_mode": "demonstration",
            "value": 30.0,
        }

        # Runtime is historical, but record should stay demo
        result = HonestEvidenceSerializer.preserve_source_mode(
            [demo_record], runtime_mode="historical_replay"
        )

        assert len(result) == 1
        assert result[0]["data_mode"] == "demonstration"

    def test_add_runtime_mode_only_when_missing(self):
        """
        Test runtime mode is added only when source mode is missing.
        Requirements: 12.3
        """
        # Record without source-level mode (legacy or external system)
        record_without_mode = {
            "source_id": "legacy_system",
            "valid_time": "2025-06-01T12:00:00Z",
            "value": 22.0,
        }

        result = HonestEvidenceSerializer.preserve_source_mode(
            [record_without_mode], runtime_mode="live"
        )

        assert len(result) == 1
        # Should add runtime mode as default
        assert result[0]["data_mode"] == "live"

    def test_mixed_source_modes_preserved(self):
        """
        Test mixed evidence preserves each source's mode.
        Requirements: 12.3
        """
        mixed_records = [
            {
                "source_id": "live_sensor",
                "valid_time": "2025-06-20T14:00:00Z",
                "data_mode": "live",
                "value": 28.0,
            },
            {
                "source_id": "historical_archive",
                "valid_time": "2024-06-20T14:00:00Z",
                "data_mode": "historical_replay",
                "value": 26.5,
            },
            {
                "source_id": "demo_generator",
                "valid_time": "2025-06-20T14:00:00Z",
                "data_mode": "demonstration",
                "value": 27.0,
            },
        ]

        # Runtime mode should NOT overwrite any source modes
        result = HonestEvidenceSerializer.preserve_source_mode(
            mixed_records, runtime_mode="live"
        )

        assert len(result) == 3
        assert result[0]["data_mode"] == "live"
        assert result[1]["data_mode"] == "historical_replay"
        assert result[2]["data_mode"] == "demonstration"

    def test_create_evidence_with_explicit_mode(self):
        """
        Test creating evidence with explicit source mode.
        Requirements: 12.3
        """
        evidence = HonestEvidenceSerializer.create_evidence_with_mode(
            source_id="weather_api",
            valid_time="2025-06-01T12:00:00Z",
            value=25.0,
            data_mode="historical_replay",
            quality="high",
        )

        assert evidence["source_id"] == "weather_api"
        assert evidence["valid_time"] == "2025-06-01T12:00:00Z"
        assert evidence["value"] == 25.0
        # Mode is source mode, not runtime mode
        assert evidence["data_mode"] == "historical_replay"
        assert evidence["quality"] == "high"


class TestDecisionMetadata:
    """
    Test decision reference metadata.
    Requirements: 12.4
    """

    def test_decision_with_all_references(self):
        """Test decision with complete references"""
        snapshot_id = uuid4()
        decision = DecisionMetadataBuilder.create_decision_reference(
            snapshot_id=snapshot_id,
            geometry_revision=3,
            engine_version="v2.1.0",
        )

        assert decision["snapshot_id"] == str(snapshot_id)
        assert decision["geometry_revision"] == 3
        assert decision["engine_version"] == "v2.1.0"

    def test_decision_preserves_unknowns_as_null(self):
        """
        Test decision preserves unknowns as null rather than fabricating.
        Requirements: 12.4
        """
        decision = DecisionMetadataBuilder.create_decision_reference(
            snapshot_id=None,
            geometry_revision=None,
            engine_version=None,
        )

        # All should be None, not fabricated values
        assert decision["snapshot_id"] is None
        assert decision["geometry_revision"] is None
        assert decision["engine_version"] is None

    def test_decision_partial_references(self):
        """Test decision with partial references"""
        decision = DecisionMetadataBuilder.create_decision_reference(
            snapshot_id=uuid4(),
            geometry_revision=None,  # Unknown
            engine_version="v1.0.0",
        )

        assert decision["snapshot_id"] is not None
        assert decision["geometry_revision"] is None  # Preserved as unknown
        assert decision["engine_version"] == "v1.0.0"


# Property-Based Tests
# Feature: backend-foundation, Property 10: Honest data modes


@given(
    runtime_mode=st.sampled_from(["live", "historical_replay", "demonstration"]),
    source_mode=st.sampled_from(["live", "historical_replay", "demonstration"]),
)
def test_property_source_mode_never_overwritten(
    runtime_mode: str, source_mode: str
):
    """
    Property 10: For any runtime and source modes, source mode is preserved.
    
    Historical and demo records retain their time and mode under every
    runtime setting.
    
    **Validates: Requirements 12.2, 12.3**
    """
    record = {
        "source_id": "test_source",
        "valid_time": "2025-06-01T12:00:00Z",
        "data_mode": source_mode,  # Source mode
        "value": 25.0,
    }

    result = HonestEvidenceSerializer.preserve_source_mode(
        [record], runtime_mode=runtime_mode
    )

    # Source mode must NEVER be overwritten by runtime mode
    assert result[0]["data_mode"] == source_mode
    # Original record should not be mutated
    assert record["data_mode"] == source_mode


@given(
    runtime_mode=st.sampled_from(["live", "historical_replay", "demonstration"])
)
def test_property_missing_mode_gets_runtime_default(runtime_mode: str):
    """
    Property 10: For records without source mode, runtime mode is added as default.
    
    **Validates: Requirements 12.3**
    """
    record_without_mode = {
        "source_id": "legacy_system",
        "valid_time": "2025-06-01T12:00:00Z",
        "value": 20.0,
        # No data_mode field
    }

    result = HonestEvidenceSerializer.preserve_source_mode(
        [record_without_mode], runtime_mode=runtime_mode
    )

    # Should add runtime mode only when missing
    assert result[0]["data_mode"] == runtime_mode
    # Original should remain unchanged
    assert "data_mode" not in record_without_mode


@given(
    num_records=st.integers(min_value=1, max_value=10),
    runtime_mode=st.sampled_from(["live", "historical_replay", "demonstration"]),
)
def test_property_multiple_records_preserve_modes(
    num_records: int, runtime_mode: str
):
    """
    Property 10: For any number of mixed evidence records, each preserves its mode.
    
    **Validates: Requirements 12.3**
    """
    modes = ["live", "historical_replay", "demonstration"]
    records = []

    for i in range(num_records):
        source_mode = modes[i % len(modes)]
        records.append(
            {
                "source_id": f"source_{i}",
                "valid_time": "2025-06-01T12:00:00Z",
                "data_mode": source_mode,
                "value": float(i),
            }
        )

    result = HonestEvidenceSerializer.preserve_source_mode(
        records, runtime_mode=runtime_mode
    )

    # Each record's source mode must be preserved
    for i, record in enumerate(result):
        expected_mode = modes[i % len(modes)]
        assert record["data_mode"] == expected_mode


@given(
    has_snapshot=st.booleans(),
    has_revision=st.booleans(),
    has_version=st.booleans(),
)
def test_property_decision_unknowns_preserved_as_null(
    has_snapshot: bool, has_revision: bool, has_version: bool
):
    """
    Property 10: For any decision, unknowns are preserved as null.
    
    **Validates: Requirements 12.4**
    """
    snapshot_id = uuid4() if has_snapshot else None
    geometry_revision = 5 if has_revision else None
    engine_version = "v1.0.0" if has_version else None

    decision = DecisionMetadataBuilder.create_decision_reference(
        snapshot_id=snapshot_id,
        geometry_revision=geometry_revision,
        engine_version=engine_version,
    )

    # Check each field is correctly preserved or null
    if has_snapshot:
        assert decision["snapshot_id"] is not None
    else:
        assert decision["snapshot_id"] is None

    if has_revision:
        assert decision["geometry_revision"] == 5
    else:
        assert decision["geometry_revision"] is None

    if has_version:
        assert decision["engine_version"] == "v1.0.0"
    else:
        assert decision["engine_version"] is None
