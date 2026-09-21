"""
Property-based tests for the Scenario Delta Engine.

Feature: climate-scenarios

Property 1: Zero-delta identity
Validates: Requirements 3.1
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from app.domain.crop_engine import rank_all
from app.domain.risk_engine import assess_all
from app.domain.snapshot_context import SnapshotContext
from app.services.scenario_service import ScenarioDelta, apply_delta


# ---------------------------------------------------------------------------
# Shared context generator (mirrors test_crop_engine_properties.py pattern)
# ---------------------------------------------------------------------------

_opt_float = st.one_of(
    st.none(),
    st.floats(min_value=-50.0, max_value=60.0, allow_nan=False, allow_infinity=False),
)

_opt_rainfall = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=5000.0, allow_nan=False, allow_infinity=False),
)

_opt_ph = st.one_of(
    st.none(),
    st.floats(min_value=3.0, max_value=10.0, allow_nan=False, allow_infinity=False),
)

_opt_pct = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False),
)

_opt_ndvi = st.one_of(
    st.none(),
    st.floats(min_value=-1.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)

_opt_vpd = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=8.0, allow_nan=False, allow_infinity=False),
)

_source = st.sampled_from(["snapshot", "demonstration"])
_data_mode = st.sampled_from(["live", "historical_replay", "demonstration"])


@st.composite
def snapshot_contexts(draw) -> SnapshotContext:
    """Generate arbitrary SnapshotContexts for scenario property testing."""
    source = draw(_source)
    snapshot_id = None if source == "demonstration" else draw(
        st.one_of(st.none(), st.uuids().map(str))
    )

    temp = draw(_opt_float)
    rainfall = draw(_opt_rainfall)
    ph = draw(_opt_ph)
    clay = draw(_opt_pct)
    sand = draw(_opt_pct)
    ndvi = draw(_opt_ndvi)
    vpd = draw(_opt_vpd)

    real_fields: set[str] = set()
    demo_fields: set[str] = set()
    all_scored = [
        "temperature_mean_c", "rainfall_total_mm", "soil_ph",
        "soil_clay_pct", "soil_sand_pct", "ndvi_mean", "vpd_kpa",
    ]
    vals = {
        "temperature_mean_c": temp,
        "rainfall_total_mm": rainfall,
        "soil_ph": ph,
        "soil_clay_pct": clay,
        "soil_sand_pct": sand,
        "ndvi_mean": ndvi,
        "vpd_kpa": vpd,
    }
    for field in all_scored:
        if vals[field] is not None:
            real_fields.add(field)
        else:
            demo_fields.add(field)

    return SnapshotContext(
        source=source,
        snapshot_id=snapshot_id,
        data_mode=draw(_data_mode),
        temperature_mean_c=temp,
        temperature_source="test" if temp is not None else None,
        rainfall_total_mm=rainfall,
        rainfall_source="test" if rainfall is not None else None,
        soil_ph=ph,
        soil_clay_pct=clay,
        soil_sand_pct=sand,
        soil_source="test" if (ph is not None or clay is not None) else None,
        soil_is_modelled=True,
        ndvi_mean=ndvi,
        ndvi_source="test" if ndvi is not None else None,
        vpd_kpa=vpd,
        real_input_fields=frozenset(real_fields),
        demonstration_input_fields=frozenset(demo_fields),
    )


# ---------------------------------------------------------------------------
# Property 1: Zero-delta identity
# Feature: climate-scenarios, Property 1: Zero-delta identity
# Validates: Requirements 3.1
# ---------------------------------------------------------------------------

ZERO_DELTA = ScenarioDelta(
    rainfall_change_pct=0.0,
    temperature_change_c=0.0,
    irrigation_mm_override=None,
)


@given(context=snapshot_contexts())
@settings(max_examples=200)
def test_property_1_zero_delta_identity(context: SnapshotContext) -> None:
    """For any SnapshotContext, applying a zero delta must produce identical
    crop scores and hazard levels to the baseline.

    Feature: climate-scenarios, Property 1: Zero-delta identity
    Validates: Requirements 3.1
    """
    scenario_context = apply_delta(context, ZERO_DELTA)

    # --- Crop scores must be identical ---
    baseline_crops = rank_all(context)
    scenario_crops = rank_all(scenario_context)

    assert len(baseline_crops) == len(scenario_crops), (
        "Zero-delta scenario changed the number of crop results."
    )

    for b, s in zip(baseline_crops, scenario_crops):
        assert b.crop_name == s.crop_name, (
            f"Zero-delta scenario changed crop ordering: "
            f"baseline={b.crop_name!r} vs scenario={s.crop_name!r}."
        )
        assert b.suitability_index == s.suitability_index, (
            f"Zero-delta scenario changed suitability_index for {b.crop_name!r}: "
            f"baseline={b.suitability_index}, scenario={s.suitability_index}."
        )
        assert b.label == s.label, (
            f"Zero-delta scenario changed label for {b.crop_name!r}: "
            f"baseline={b.label!r}, scenario={s.label!r}."
        )

    # --- Hazard levels must be identical ---
    baseline_risk = assess_all(context, "test-farm-id")
    scenario_risk = assess_all(scenario_context, "test-farm-id")

    assert len(baseline_risk.assessments) == len(scenario_risk.assessments), (
        "Zero-delta scenario changed the number of hazard assessments."
    )

    for b_hazard, s_hazard in zip(baseline_risk.assessments, scenario_risk.assessments):
        assert b_hazard.hazard == s_hazard.hazard, (
            f"Zero-delta scenario changed hazard ordering: "
            f"baseline={b_hazard.hazard!r} vs scenario={s_hazard.hazard!r}."
        )
        assert b_hazard.level == s_hazard.level, (
            f"Zero-delta scenario changed hazard level for {b_hazard.hazard!r}: "
            f"baseline={b_hazard.level!r}, scenario={s_hazard.level!r}."
        )
        assert b_hazard.index == s_hazard.index, (
            f"Zero-delta scenario changed hazard index for {b_hazard.hazard!r}: "
            f"baseline={b_hazard.index}, scenario={s_hazard.index}."
        )
