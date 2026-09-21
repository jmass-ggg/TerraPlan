"""
Property-based tests for the Farm Risk Engine.

Feature: hazards-actions

Property 1: Hazard level matches threshold band
Validates: Requirements 2.2, 2.3, 2.4, 3.2, 3.3, 3.4, 4.2, 4.3, 4.4, 6.2, 6.3, 6.4
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from hypothesis import given, settings, strategies as st

from app.domain.risk_engine import (
    DROUGHT_HIGH_RATIO,
    DROUGHT_MEDIUM_RATIO,
    HEAT_HIGH_TEMP,
    HEAT_MEDIUM_TEMP,
    HEAVY_RAIN_HIGH_MM,
    HEAVY_RAIN_MEDIUM_MM,
    LEVEL_HIGH,
    LEVEL_LOW,
    LEVEL_MEDIUM,
    LEVEL_UNKNOWN,
    _assess_drought,
    _assess_flood,
    _assess_heat,
    _assess_heavy_rainfall,
    _assess_wind,
    WIND_HIGH_MS,
    WIND_MEDIUM_MS,
)
from app.domain.snapshot_context import SnapshotContext, context_for_risks


# ---------------------------------------------------------------------------
# Shared context builder helper
# ---------------------------------------------------------------------------

def _make_context(
    *,
    rainfall_total_mm: float | None = None,
    climate_baseline_rainfall_mm: float | None = None,
    temperature_mean_c: float | None = None,
    rain_7d_mm: float | None = None,
    wind_max_ms: float | None = None,
    slope_pct: float | None = None,
    vpd_kpa: float | None = None,
    ndvi_mean: float | None = None,
) -> SnapshotContext:
    """Build a minimal SnapshotContext for testing individual hazard assessors."""
    real_fields: set[str] = set()
    demo_fields: set[str] = set()

    field_vals = {
        "rainfall_total_mm": rainfall_total_mm,
        "temperature_mean_c": temperature_mean_c,
        "vpd_kpa": vpd_kpa,
        "ndvi_mean": ndvi_mean,
    }
    for fname, val in field_vals.items():
        if val is not None:
            real_fields.add(fname)
        else:
            demo_fields.add(fname)

    return SnapshotContext(
        source="snapshot",
        snapshot_id="test-snapshot-id",
        data_mode="live",
        temperature_mean_c=temperature_mean_c,
        temperature_source="test" if temperature_mean_c is not None else None,
        rainfall_total_mm=rainfall_total_mm,
        rainfall_source="test" if rainfall_total_mm is not None else None,
        soil_ph=None,
        soil_clay_pct=None,
        soil_sand_pct=None,
        soil_source=None,
        soil_is_modelled=False,
        ndvi_mean=ndvi_mean,
        ndvi_source="test" if ndvi_mean is not None else None,
        vpd_kpa=vpd_kpa,
        real_input_fields=frozenset(real_fields),
        demonstration_input_fields=frozenset(demo_fields),
        rain_7d_mm=rain_7d_mm,
        wind_max_ms=wind_max_ms,
        slope_pct=slope_pct,
        climate_baseline_rainfall_mm=climate_baseline_rainfall_mm,
    )


# ---------------------------------------------------------------------------
# Property 1: Hazard level matches threshold band
# Feature: hazards-actions, Property 1: Hazard level matches threshold band
# Validates: Requirements 2.2, 2.3, 2.4, 3.2, 3.3, 3.4, 4.2, 4.3, 4.4, 6.2, 6.3, 6.4
# ---------------------------------------------------------------------------

# ---- Drought ---------------------------------------------------------------

@given(
    rainfall=st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
    baseline=st.floats(min_value=1.0, max_value=2000.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=200)
def test_property_1_drought_level_matches_threshold(rainfall: float, baseline: float) -> None:
    """For any valid rainfall and baseline pair, the drought level must match the documented band.

    Feature: hazards-actions, Property 1: Hazard level matches threshold band
    Validates: Requirements 2.2, 2.3, 2.4
    """
    context = _make_context(
        rainfall_total_mm=rainfall,
        climate_baseline_rainfall_mm=baseline,
    )
    result = _assess_drought(context)

    ratio = rainfall / baseline

    if ratio < DROUGHT_HIGH_RATIO:
        expected = LEVEL_HIGH
    elif ratio < DROUGHT_MEDIUM_RATIO:
        expected = LEVEL_MEDIUM
    else:
        expected = LEVEL_LOW

    assert result.level == expected, (
        f"Drought level mismatch: rainfall={rainfall:.2f}, baseline={baseline:.2f}, "
        f"ratio={ratio:.4f}. Expected {expected!r}, got {result.level!r}."
    )


# ---- Heat ------------------------------------------------------------------

@given(
    temp=st.floats(min_value=-20.0, max_value=60.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=200)
def test_property_1_heat_level_matches_threshold(temp: float) -> None:
    """For any valid mean temperature, the heat level must match the documented threshold band.

    Feature: hazards-actions, Property 1: Hazard level matches threshold band
    Validates: Requirements 3.2, 3.3, 3.4
    """
    context = _make_context(temperature_mean_c=temp)
    result = _assess_heat(context)

    if temp > HEAT_HIGH_TEMP:
        expected = LEVEL_HIGH
    elif temp > HEAT_MEDIUM_TEMP:
        expected = LEVEL_MEDIUM
    else:
        expected = LEVEL_LOW

    assert result.level == expected, (
        f"Heat level mismatch: temp={temp:.2f}°C. Expected {expected!r}, got {result.level!r}."
    )


# ---- Heavy Rainfall --------------------------------------------------------

@given(
    rain_7d=st.floats(min_value=0.0, max_value=500.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=200)
def test_property_1_heavy_rainfall_level_matches_threshold(rain_7d: float) -> None:
    """For any valid 7-day rainfall value, the heavy_rainfall level must match the documented band.

    Feature: hazards-actions, Property 1: Hazard level matches threshold band
    Validates: Requirements 4.2, 4.3, 4.4
    """
    context = _make_context(rain_7d_mm=rain_7d)
    result = _assess_heavy_rainfall(context)

    if rain_7d > HEAVY_RAIN_HIGH_MM:
        expected = LEVEL_HIGH
    elif rain_7d > HEAVY_RAIN_MEDIUM_MM:
        expected = LEVEL_MEDIUM
    else:
        expected = LEVEL_LOW

    assert result.level == expected, (
        f"Heavy rainfall level mismatch: rain_7d={rain_7d:.2f} mm. "
        f"Expected {expected!r}, got {result.level!r}."
    )


# ---- Wind ------------------------------------------------------------------

@given(
    wind_max=st.floats(min_value=0.0, max_value=60.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=200)
def test_property_1_wind_level_matches_threshold(wind_max: float) -> None:
    """For any valid maximum wind speed, the wind level must match the documented threshold band.

    Feature: hazards-actions, Property 1: Hazard level matches threshold band
    Validates: Requirements 6.2, 6.3, 6.4
    """
    context = _make_context(wind_max_ms=wind_max)
    result = _assess_wind(context)

    if wind_max > WIND_HIGH_MS:
        expected = LEVEL_HIGH
    elif wind_max > WIND_MEDIUM_MS:
        expected = LEVEL_MEDIUM
    else:
        expected = LEVEL_LOW

    assert result.level == expected, (
        f"Wind level mismatch: wind_max={wind_max:.2f} m/s. "
        f"Expected {expected!r}, got {result.level!r}."
    )


def test_drought_index_is_bounded_monotonic_and_does_not_claim_ndvi_decline() -> None:
    rainfall_values = [150.0, 100.0, 80.0, 60.0, 30.0, 0.0]
    results = [
        _assess_drought(
            _make_context(
                rainfall_total_mm=rainfall,
                climate_baseline_rainfall_mm=100.0,
                ndvi_mean=0.2,
            )
        )
        for rainfall in rainfall_values
    ]

    assert [result.index for result in results] == [0, 0, 20, 40, 70, 100]
    assert all(0 <= result.index <= 100 for result in results)
    assert all(result.driver != "ndvi_decline" for result in results)
    assert results[2].driver == "low_ndvi"
    assert "ratio" not in results[0].explanation
    assert "above baseline" in results[0].explanation
    assert "rainfall deficit" in results[-1].explanation


def test_missing_critical_evidence_stays_unknown() -> None:
    assert _assess_drought(_make_context()).level == LEVEL_UNKNOWN
    assert _assess_heat(_make_context()).level == LEVEL_UNKNOWN
    assert _assess_heavy_rainfall(_make_context()).level == LEVEL_UNKNOWN
    assert _assess_flood(_make_context(), LEVEL_LOW).level == LEVEL_UNKNOWN
    assert _assess_wind(_make_context()).level == LEVEL_UNKNOWN


def test_hazard_indices_are_bounded_and_monotonic() -> None:
    heat = [_assess_heat(_make_context(temperature_mean_c=value)).index for value in (0, 20, 30, 33, 40, 60)]
    heavy_rain = [_assess_heavy_rainfall(_make_context(rain_7d_mm=value)).index for value in (0, 60, 120, 240)]
    wind = [_assess_wind(_make_context(wind_max_ms=value)).index for value in (0, 8, 15, 20, 40)]

    assert heat == sorted(heat)
    assert heavy_rain == sorted(heavy_rain)
    assert wind == sorted(wind)
    assert all(0 <= index <= 100 for index in (*heat, *heavy_rain, *wind))


def test_risk_context_converts_monthly_climate_totals_to_matched_seven_days() -> None:
    snapshot = SimpleNamespace(
        id="snapshot-id",
        data_mode="live",
        valid_time_utc=datetime(2026, 9, 28, tzinfo=UTC),
        climate_baseline={
            "aggregation_version": "monthly-totals-v2",
            "all_monthly_means": {
                "temperature_2m_mean": {"9": 20.0, "10": 20.0},
                "precipitation_sum": {"9": 30.0, "10": 62.0},
            },
        },
        weather={
            "fields": {
                "temperature_2m": {"value": 20.0, "quality": "accepted", "data_mode": "live"},
            },
            "daily": {
                "precipitation_sum": [1.0] * 7,
                "wind_speed_10m_max": [3.6] * 7,
            },
            "daily_units": {"wind_speed_10m_max": "km/h"},
        },
        soil={},
        satellite={},
        conduit={},
        terrain={},
    )

    context = context_for_risks(snapshot)

    # Sep 28–30: 30 mm / 30 days, Oct 1–4: 62 mm / 31 days.
    assert context.climate_baseline_rainfall_mm == 11.0
    assert context.rainfall_total_mm == 7.0
