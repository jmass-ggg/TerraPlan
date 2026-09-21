"""
Unit tests for the Conduit fixture parser.

Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 3.1, 3.5
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import pytest

from app.data.conduit.parser import (
    ConduitFormatError,
    ParsedObservation,
    ParseReport,
    parse_fixture,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wrap(records: list[dict]) -> str:
    """Wrap records in the fixture envelope format."""
    return json.dumps({"status": "success", "data": records})


def _valid_record(**overrides) -> dict:
    """Return a minimal valid fixture record."""
    base = {
        "ts": "2025-06-01T00:00:07Z",
        "temp_bmx": "13.7",
        "temp_mcp": "13.9",
        "temp_sht": "14.1",
        "humidity_sht": "90.5",
        "wind_spd": "0",
        "wind_dir": "128",
        "wind_gust": "0",
        "press_bmx": "853.3",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Root structure (Requirements 2.7)
# ---------------------------------------------------------------------------

class TestRootStructure:
    def test_array_root_raises(self):
        with pytest.raises(ConduitFormatError, match="JSON object"):
            parse_fixture(json.dumps([{"ts": "2025-06-01T00:00:07Z"}]))

    def test_string_root_raises(self):
        with pytest.raises(ConduitFormatError, match="JSON object"):
            parse_fixture(json.dumps("bad"))

    def test_missing_data_key_raises(self):
        with pytest.raises(ConduitFormatError, match="'data' key"):
            parse_fixture(json.dumps({"status": "success"}))

    def test_non_array_data_raises(self):
        with pytest.raises(ConduitFormatError, match="JSON array"):
            parse_fixture(json.dumps({"data": "not-an-array"}))

    def test_invalid_json_raises(self):
        with pytest.raises(ConduitFormatError, match="valid JSON"):
            parse_fixture("{not valid json}")

    def test_empty_array_returns_empty(self):
        obs, report = parse_fixture(json.dumps({"data": []}))
        assert obs == []
        assert report.accepted == 0
        assert report.rejected == 0


# ---------------------------------------------------------------------------
# Timestamp parsing (Requirements 2.2, 2.3, 3.1, 3.5)
# ---------------------------------------------------------------------------

class TestTimestampParsing:
    def test_valid_z_timestamp_parsed_to_utc(self):
        obs, _ = parse_fixture(_wrap([_valid_record(ts="2025-06-01T00:00:07Z")]))
        assert len(obs) == 1
        assert obs[0].ts == datetime(2025, 6, 1, 0, 0, 7, tzinfo=timezone.utc)
        assert obs[0].ts.tzinfo is not None

    def test_valid_offset_timestamp_normalised_to_utc(self):
        # +03:00 → subtract 3h
        obs, _ = parse_fixture(_wrap([_valid_record(ts="2025-06-01T03:00:07+03:00")]))
        assert obs[0].ts == datetime(2025, 6, 1, 0, 0, 7, tzinfo=timezone.utc)

    def test_missing_ts_rejects_record(self):
        record = _valid_record()
        del record["ts"]
        obs, report = parse_fixture(_wrap([record]))
        assert len(obs) == 0
        assert report.rejected == 1
        assert any(e.field == "ts" for e in report.errors)

    def test_null_ts_rejects_record(self):
        obs, report = parse_fixture(_wrap([_valid_record(ts=None)]))
        assert len(obs) == 0
        assert report.rejected == 1

    def test_unparseable_ts_rejects_record(self):
        obs, report = parse_fixture(_wrap([_valid_record(ts="not-a-date")]))
        assert len(obs) == 0
        assert report.rejected == 1
        err = report.errors[0]
        assert err.field == "ts"
        assert "not-a-date" in err.reason

    def test_ts_before_2020_rejects_record(self):
        obs, report = parse_fixture(_wrap([_valid_record(ts="2019-12-31T23:59:59Z")]))
        assert len(obs) == 0
        assert report.rejected == 1

    def test_future_ts_rejects_record(self):
        obs, report = parse_fixture(_wrap([_valid_record(ts="2099-01-01T00:00:00Z")]))
        assert len(obs) == 0
        assert report.rejected == 1


# ---------------------------------------------------------------------------
# Record continuation (Requirements 2.2 — ingestion continues on bad records)
# ---------------------------------------------------------------------------

class TestIngestionContinues:
    def test_bad_record_does_not_halt_run(self):
        records = [
            _valid_record(ts="missing"),          # rejected
            _valid_record(ts="2025-06-01T01:00:00Z"),  # accepted
            _valid_record(ts="2025-06-01T02:00:00Z"),  # accepted
        ]
        obs, report = parse_fixture(_wrap(records))
        assert len(obs) == 2
        assert report.accepted == 2
        assert report.rejected == 1

    def test_non_object_record_is_rejected(self):
        payload = json.dumps({"data": ["not-a-dict"]})
        obs, report = parse_fixture(payload)
        assert len(obs) == 0
        assert report.rejected == 1


# ---------------------------------------------------------------------------
# Numeric field parsing (Requirements 2.4, 2.5)
# ---------------------------------------------------------------------------

class TestNumericFields:
    def test_string_numeric_coerced(self):
        obs, report = parse_fixture(_wrap([_valid_record(temp_bmx="14.5")]))
        assert obs[0].fields["temp_bmx"] == pytest.approx(14.5)
        assert report.field_errors == 0

    def test_non_finite_nan_becomes_none(self):
        # JSON doesn't natively support NaN; pass it via Python dict then manually
        # construct JSON with a string NaN (as the fixture might expose it).
        record = _valid_record(temp_bmx="NaN")
        obs, report = parse_fixture(_wrap([record]))
        # record accepted (ts is valid), but temp_bmx field is None
        assert len(obs) == 1
        assert obs[0].fields["temp_bmx"] is None
        assert report.field_errors == 1

    def test_non_finite_inf_string_becomes_none(self):
        record = _valid_record(temp_bmx="Inf")
        obs, report = parse_fixture(_wrap([record]))
        assert obs[0].fields["temp_bmx"] is None

    def test_unparseable_string_becomes_none(self):
        record = _valid_record(temp_bmx="not-a-number")
        obs, report = parse_fixture(_wrap([record]))
        assert len(obs) == 1
        assert obs[0].fields["temp_bmx"] is None
        assert report.field_errors == 1

    def test_null_field_becomes_none(self):
        record = _valid_record(temp_bmx=None)
        obs, report = parse_fixture(_wrap([record]))
        assert obs[0].fields["temp_bmx"] is None
        assert report.field_errors == 1  # null counts as a field error

    def test_absent_field_becomes_none_no_error(self):
        record = _valid_record()
        record.pop("wind_spd", None)
        obs, report = parse_fixture(_wrap([record]))
        assert obs[0].fields["wind_spd"] is None
        # absent (not present at all) → no field error; QC assigns "missing"
        assert report.field_errors == 0

    def test_integer_value_parsed(self):
        record = _valid_record(si1145_vis=262)
        obs, _ = parse_fixture(_wrap([record]))
        assert obs[0].fields["si1145_vis"] == pytest.approx(262.0)


# ---------------------------------------------------------------------------
# Parse report (Requirements 2.6)
# ---------------------------------------------------------------------------

class TestParseReport:
    def test_report_counts_match(self):
        records = [
            _valid_record(ts="2025-06-01T00:00:07Z", temp_bmx="NaN"),  # accepted, 1 field err
            _valid_record(ts="bad"),                                     # rejected
            _valid_record(ts="2025-06-01T01:00:07Z"),                   # accepted, 0 field err
        ]
        obs, report = parse_fixture(_wrap(records))
        assert report.accepted == 2
        assert report.rejected == 1
        assert report.field_errors == 1
        assert len(report.errors) == 2  # 1 ts error + 1 field error

    def test_parse_errors_contain_reason(self):
        obs, report = parse_fixture(_wrap([_valid_record(ts="bad-ts")]))
        assert report.rejected == 1
        assert len(report.errors) >= 1
        assert report.errors[0].reason  # non-empty reason string


# ---------------------------------------------------------------------------
# Actual fixture smoke test
# ---------------------------------------------------------------------------

class TestActualFixture:
    def test_fixture_parses_successfully(self):
        import os
        # tests/ is 3 levels below workspace root: tests/ → backend/ → farmtwin/ → root
        fixture_path = os.path.join(
            os.path.dirname(__file__), "../../..", "data/samples/weather.json"
        )
        with open(fixture_path) as f:
            raw = f.read()
        obs, report = parse_fixture(raw)
        assert report.accepted == 191
        assert report.rejected == 0
        # All ts values should be UTC-aware datetimes
        for o in obs:
            assert o.ts.tzinfo is not None
            assert o.ts.tzinfo == timezone.utc
