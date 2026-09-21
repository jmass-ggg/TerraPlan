"""
Unit tests for the Conduit Normalizer and QC Engine.

Requirements: 4.1–4.7, 5.1–5.9, 7.1–7.4, 9.1–9.5
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone

import pytest

from app.data.conduit.normalizer import (
    NORMALIZER_VERSION,
    _apply_spread_check,
    _compute_temperature_consensus,
    _qc_humidity,
    _qc_temperature,
    _qc_wind_direction,
    _qc_wind_speed,
    compute_vpd_kpa,
    normalize_and_qc,
)
from app.data.conduit.parser import ParsedObservation
from app.models.conduit import NormalizedObservation, QualityFlag, Station


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_station() -> Station:
    s = Station()
    s.id = uuid.uuid4()
    s.provider_station_id = "test-station-001"
    s.name = "Test Station"
    s.provider = "conduit"
    return s


def _make_obs(**field_overrides) -> ParsedObservation:
    """Return a ParsedObservation with all standard fields present."""
    base_fields: dict = {
        "temp_bmx": 20.0,
        "temp_mcp": 20.1,
        "temp_sht": 20.2,
        "humidity_sht": 65.0,
        "wind_spd": 2.5,
        "wind_dir": 180.0,
        "wind_gust": 3.0,
        "press_bmx": 853.0,
        "si1145_vis": 100.0,
        "si1145_ir": 80.0,
        "si1145_uv": 5.0,
        "rg1": 0.0,
        "rg2": 0.0,
        "rg1tt": 0.0,
        "rg2tt": 0.0,
        "rg1tp": 0.0,
        "rg2tp": 0.0,
    }
    base_fields.update(field_overrides)
    return ParsedObservation(
        raw_record={},
        ts=datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        fields=base_fields,
    )


def _normalize(obs: ParsedObservation) -> NormalizedObservation:
    station = _make_station()
    run_id = uuid.uuid4()
    return normalize_and_qc(obs, station, run_id)


# ---------------------------------------------------------------------------
# VPD formula (Requirements 9.1, 9.4)
# ---------------------------------------------------------------------------

class TestVpdFormula:
    def test_known_value(self):
        # T=25, RH=60 → es=3.1671, vpd=1.2668 kPa (approx)
        vpd = compute_vpd_kpa(25.0, 60.0)
        expected = round(0.6108 * math.exp((17.27 * 25.0) / (25.0 + 237.3)) * (1 - 60.0 / 100.0), 4)
        assert vpd == pytest.approx(expected, abs=1e-6)

    def test_100_percent_humidity_gives_zero_vpd(self):
        vpd = compute_vpd_kpa(20.0, 100.0)
        assert vpd == pytest.approx(0.0, abs=1e-6)

    def test_zero_humidity_gives_max_vpd(self):
        vpd = compute_vpd_kpa(20.0, 0.0)
        expected = round(0.6108 * math.exp((17.27 * 20.0) / (20.0 + 237.3)), 4)
        assert vpd == pytest.approx(expected, abs=1e-6)

    def test_result_rounded_to_4dp(self):
        vpd = compute_vpd_kpa(22.5, 75.0)
        # Ensure result has at most 4 decimal places
        assert vpd == round(vpd, 4)


# ---------------------------------------------------------------------------
# Temperature QC (Requirements 5.2)
# ---------------------------------------------------------------------------

class TestQcTemperature:
    def test_in_range_accepted(self):
        assert _qc_temperature(20.0) == QualityFlag.ACCEPTED

    def test_below_min_suspect(self):
        assert _qc_temperature(-11.0) == QualityFlag.SUSPECT

    def test_above_max_suspect(self):
        assert _qc_temperature(61.0) == QualityFlag.SUSPECT

    def test_at_boundary_accepted(self):
        assert _qc_temperature(-10.0) == QualityFlag.ACCEPTED
        assert _qc_temperature(60.0) == QualityFlag.ACCEPTED

    def test_none_is_missing(self):
        assert _qc_temperature(None) == QualityFlag.MISSING


# ---------------------------------------------------------------------------
# Humidity QC (Requirements 5.3)
# ---------------------------------------------------------------------------

class TestQcHumidity:
    def test_in_range_accepted(self):
        assert _qc_humidity(65.0) == QualityFlag.ACCEPTED

    def test_above_100_invalid(self):
        assert _qc_humidity(101.0) == QualityFlag.INVALID

    def test_below_0_invalid(self):
        assert _qc_humidity(-1.0) == QualityFlag.INVALID

    def test_boundary_values_accepted(self):
        assert _qc_humidity(0.0) == QualityFlag.ACCEPTED
        assert _qc_humidity(100.0) == QualityFlag.ACCEPTED

    def test_none_is_missing(self):
        assert _qc_humidity(None) == QualityFlag.MISSING


# ---------------------------------------------------------------------------
# Wind QC (Requirements 5.4, 5.5)
# ---------------------------------------------------------------------------

class TestQcWind:
    def test_valid_speed_accepted(self):
        assert _qc_wind_speed(5.0) == QualityFlag.ACCEPTED

    def test_zero_speed_accepted(self):
        assert _qc_wind_speed(0.0) == QualityFlag.ACCEPTED

    def test_negative_speed_invalid(self):
        assert _qc_wind_speed(-0.1) == QualityFlag.INVALID

    def test_none_speed_missing(self):
        assert _qc_wind_speed(None) == QualityFlag.MISSING

    def test_valid_direction_accepted(self):
        assert _qc_wind_direction(180.0) == QualityFlag.ACCEPTED

    def test_zero_direction_accepted(self):
        assert _qc_wind_direction(0.0) == QualityFlag.ACCEPTED

    def test_360_direction_accepted(self):
        assert _qc_wind_direction(360.0) == QualityFlag.ACCEPTED

    def test_above_360_invalid(self):
        assert _qc_wind_direction(361.0) == QualityFlag.INVALID

    def test_negative_direction_invalid(self):
        assert _qc_wind_direction(-1.0) == QualityFlag.INVALID

    def test_none_direction_missing(self):
        assert _qc_wind_direction(None) == QualityFlag.MISSING


# ---------------------------------------------------------------------------
# Temperature consensus (Requirements 7.1–7.4)
# ---------------------------------------------------------------------------

class TestTemperatureConsensus:
    def test_three_accepted_uses_median(self):
        val, q, count = _compute_temperature_consensus(
            10.0, 12.0, 14.0,
            QualityFlag.ACCEPTED, QualityFlag.ACCEPTED, QualityFlag.ACCEPTED,
        )
        assert val == pytest.approx(12.0)
        assert q == QualityFlag.ACCEPTED
        assert count == 3

    def test_two_accepted_uses_median(self):
        val, q, count = _compute_temperature_consensus(
            10.0, 14.0, None,
            QualityFlag.ACCEPTED, QualityFlag.ACCEPTED, QualityFlag.MISSING,
        )
        assert val == pytest.approx(12.0)
        assert q == QualityFlag.ACCEPTED
        assert count == 2

    def test_one_accepted_single_channel(self):
        val, q, count = _compute_temperature_consensus(
            20.0, None, None,
            QualityFlag.ACCEPTED, QualityFlag.MISSING, QualityFlag.MISSING,
        )
        assert val == pytest.approx(20.0)
        assert q == QualityFlag.SINGLE_CHANNEL
        assert count == 1

    def test_no_accepted_missing(self):
        val, q, count = _compute_temperature_consensus(
            None, None, None,
            QualityFlag.MISSING, QualityFlag.MISSING, QualityFlag.MISSING,
        )
        assert val is None
        assert q == QualityFlag.MISSING
        assert count == 0

    def test_suspect_channels_excluded(self):
        # All three present but one is SUSPECT — only 2 contribute
        val, q, count = _compute_temperature_consensus(
            10.0, 12.0, 20.0,
            QualityFlag.ACCEPTED, QualityFlag.ACCEPTED, QualityFlag.SUSPECT,
        )
        assert val == pytest.approx(11.0)  # median of [10, 12]
        assert q == QualityFlag.ACCEPTED
        assert count == 2


# ---------------------------------------------------------------------------
# Inter-sensor spread check (Requirements 5.6)
# ---------------------------------------------------------------------------

class TestSpreadCheck:
    def test_spread_within_threshold_unchanged(self):
        bmx_q, mcp_q, sht_q = _apply_spread_check(
            20.0, 20.5, 21.0,
            QualityFlag.ACCEPTED, QualityFlag.ACCEPTED, QualityFlag.ACCEPTED,
        )
        assert bmx_q == QualityFlag.ACCEPTED
        assert mcp_q == QualityFlag.ACCEPTED
        assert sht_q == QualityFlag.ACCEPTED

    def test_spread_over_threshold_flags_outlier(self):
        # temp_sht is 4°C above the mean → highest deviation
        bmx_q, mcp_q, sht_q = _apply_spread_check(
            20.0, 20.0, 24.0,
            QualityFlag.ACCEPTED, QualityFlag.ACCEPTED, QualityFlag.ACCEPTED,
        )
        assert sht_q == QualityFlag.SUSPECT
        assert bmx_q == QualityFlag.ACCEPTED
        assert mcp_q == QualityFlag.ACCEPTED

    def test_not_all_accepted_skips_check(self):
        bmx_q, mcp_q, sht_q = _apply_spread_check(
            20.0, 20.0, 30.0,
            QualityFlag.ACCEPTED, QualityFlag.ACCEPTED, QualityFlag.MISSING,
        )
        # MISSING channel → spread check skipped, no SUSPECT assigned
        assert bmx_q == QualityFlag.ACCEPTED
        assert mcp_q == QualityFlag.ACCEPTED
        assert sht_q == QualityFlag.MISSING


# ---------------------------------------------------------------------------
# Full normalize_and_qc integration (Requirements 4.x, 5.x, 7.x, 9.x)
# ---------------------------------------------------------------------------

class TestNormalizeAndQc:
    def test_happy_path_returns_observation(self):
        norm = _normalize(_make_obs())
        assert isinstance(norm, NormalizedObservation)
        assert norm.data_mode == "historical_replay"
        assert norm.valid_time_utc == datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_station_and_run_ids_set(self):
        station = _make_station()
        run_id = uuid.uuid4()
        norm = normalize_and_qc(_make_obs(), station, run_id)
        assert norm.station_id == station.id
        assert norm.ingestion_run_id == run_id

    def test_temperature_channels_mapped(self):
        norm = _normalize(_make_obs(temp_bmx=19.0, temp_mcp=20.0, temp_sht=21.0))
        assert norm.temp_bmx_celsius == pytest.approx(19.0)
        assert norm.temp_mcp_celsius == pytest.approx(20.0)
        assert norm.temp_sht_celsius == pytest.approx(21.0)
        assert norm.temp_bmx_quality == QualityFlag.ACCEPTED

    def test_humidity_mapped(self):
        norm = _normalize(_make_obs(humidity_sht=70.0))
        assert norm.humidity_sht_pct == pytest.approx(70.0)
        assert norm.humidity_sht_quality == QualityFlag.ACCEPTED

    def test_humidity_out_of_range_invalid(self):
        norm = _normalize(_make_obs(humidity_sht=105.0))
        assert norm.humidity_sht_quality == QualityFlag.INVALID

    def test_wind_mapped(self):
        norm = _normalize(_make_obs(wind_spd=3.0, wind_gust=5.0, wind_dir=90.0))
        assert norm.wind_spd_ms == pytest.approx(3.0)
        assert norm.wind_gust_ms == pytest.approx(5.0)
        assert norm.wind_dir_deg == pytest.approx(90.0)
        assert norm.wind_spd_quality == QualityFlag.ACCEPTED
        assert norm.wind_gust_quality == QualityFlag.ACCEPTED
        assert norm.wind_dir_quality == QualityFlag.ACCEPTED

    def test_negative_wind_invalid(self):
        norm = _normalize(_make_obs(wind_spd=-1.0))
        assert norm.wind_spd_quality == QualityFlag.INVALID

    def test_vpd_computed_with_valid_inputs(self):
        norm = _normalize(_make_obs(temp_bmx=25.0, temp_mcp=25.0, temp_sht=25.0, humidity_sht=60.0))
        expected = compute_vpd_kpa(25.0, 60.0)
        assert norm.vpd_kpa == pytest.approx(expected, abs=1e-6)
        assert norm.vpd_quality == QualityFlag.ACCEPTED

    def test_vpd_null_when_humidity_invalid(self):
        norm = _normalize(_make_obs(humidity_sht=110.0))
        assert norm.vpd_kpa is None
        assert norm.vpd_quality == QualityFlag.MISSING

    def test_vpd_null_when_no_temp(self):
        norm = _normalize(_make_obs(temp_bmx=None, temp_mcp=None, temp_sht=None))
        assert norm.vpd_kpa is None
        assert norm.vpd_quality == QualityFlag.MISSING

    def test_rainfall_always_unconfirmed(self):
        """Requirements 4.7: rainfall fields must always be UNCONFIRMED."""
        norm = _normalize(_make_obs(rg1=5.0, rg2=3.0))
        assert norm.rg1_raw == pytest.approx(5.0)
        assert norm.rg1_quality == QualityFlag.UNCONFIRMED
        assert norm.rg2_quality == QualityFlag.UNCONFIRMED
        assert norm.rg1tt_quality == QualityFlag.UNCONFIRMED
        assert norm.rg2tt_quality == QualityFlag.UNCONFIRMED
        assert norm.rg1tp_quality == QualityFlag.UNCONFIRMED
        assert norm.rg2tp_quality == QualityFlag.UNCONFIRMED

    def test_missing_fields_get_missing_quality(self):
        """Fields absent from obs.fields produce MISSING quality."""
        norm = _normalize(_make_obs(
            temp_bmx=None, wind_spd=None, humidity_sht=None,
        ))
        assert norm.temp_bmx_quality == QualityFlag.MISSING
        assert norm.wind_spd_quality == QualityFlag.MISSING
        assert norm.humidity_sht_quality == QualityFlag.MISSING

    def test_consensus_channel_count_stored(self):
        norm = _normalize(_make_obs(temp_bmx=20.0, temp_mcp=20.1, temp_sht=20.2))
        assert norm.temperature_channel_count == 3

    def test_single_channel_consensus(self):
        norm = _normalize(_make_obs(temp_bmx=20.0, temp_mcp=None, temp_sht=None))
        assert norm.temperature_consensus == pytest.approx(20.0)
        assert norm.temperature_consensus_quality == QualityFlag.SINGLE_CHANNEL
        assert norm.temperature_channel_count == 1

    def test_pressure_mapped(self):
        norm = _normalize(_make_obs(press_bmx=850.5))
        assert norm.press_hpa == pytest.approx(850.5)
        assert norm.press_quality == QualityFlag.ACCEPTED

    def test_light_sensors_mapped(self):
        norm = _normalize(_make_obs(si1145_vis=200.0, si1145_ir=150.0, si1145_uv=10.0))
        assert norm.si1145_vis_raw == pytest.approx(200.0)
        assert norm.si1145_ir_raw == pytest.approx(150.0)
        assert norm.si1145_uv_raw == pytest.approx(10.0)
        assert norm.si1145_vis_quality == QualityFlag.ACCEPTED

    def test_out_of_range_temperature_suspect(self):
        norm = _normalize(_make_obs(temp_bmx=65.0))
        assert norm.temp_bmx_quality == QualityFlag.SUSPECT
