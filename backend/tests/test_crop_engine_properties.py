"""
Property-based tests for the crop suitability engine.

Feature: crop-simulator
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from app.domain.crop_engine import score
from app.domain.crop_register import CROP_REGISTER
from app.domain.snapshot_context import SnapshotContext


# ---------------------------------------------------------------------------
# Generators
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
    """Generate arbitrary SnapshotContexts with any combination of null/real values."""
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
    all_scored = ["temperature_mean_c", "rainfall_total_mm", "soil_ph",
                  "soil_clay_pct", "soil_sand_pct", "ndvi_mean", "vpd_kpa"]
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


_crops = st.sampled_from(CROP_REGISTER)


@st.composite
def exclusion_contexts(draw, crop) -> SnapshotContext:
    """Generate SnapshotContexts where temperature exceeds the hard exclusion threshold."""
    # temperature_mean_c > heat_tolerance_ceiling_c + 8
    min_temp = crop.heat_tolerance_ceiling_c + 8.0 + 0.001
    temp = draw(
        st.floats(min_value=min_temp, max_value=min_temp + 40.0, allow_nan=False, allow_infinity=False)
    )

    rainfall = draw(_opt_rainfall)
    ph = draw(_opt_ph)
    clay = draw(_opt_pct)
    sand = draw(_opt_pct)
    ndvi = draw(_opt_ndvi)
    vpd = draw(_opt_vpd)

    real_fields: set[str] = {"temperature_mean_c"}
    demo_fields: set[str] = set()
    field_vals = {
        "rainfall_total_mm": rainfall,
        "soil_ph": ph,
        "soil_clay_pct": clay,
        "soil_sand_pct": sand,
        "ndvi_mean": ndvi,
        "vpd_kpa": vpd,
    }
    for field, val in field_vals.items():
        if val is not None:
            real_fields.add(field)
        else:
            demo_fields.add(field)

    return SnapshotContext(
        source="snapshot",
        snapshot_id=None,
        data_mode="historical_replay",
        temperature_mean_c=temp,
        temperature_source="test",
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
# Property 1: Determinism
# Feature: crop-simulator, Property 1: Determinism
# Validates: Requirements 3.7
# ---------------------------------------------------------------------------

@given(crop=_crops, context=snapshot_contexts())
@settings(max_examples=200)
def test_property_1_determinism(crop, context):
    """For any crop and SnapshotContext, calling score() twice returns identical results.

    Feature: crop-simulator, Property 1: Determinism
    Validates: Requirements 3.7
    """
    result_a = score(crop, context)
    result_b = score(crop, context)

    assert result_a == result_b, (
        f"score() is not deterministic for crop={crop.name!r}. "
        f"First call: suitability_index={result_a.suitability_index}, "
        f"Second call: suitability_index={result_b.suitability_index}."
    )


# ---------------------------------------------------------------------------
# Property 2: Hard exclusion zeroes the score
# Feature: crop-simulator, Property 2: Hard exclusion zeroes the score
# Validates: Requirements 3.3
# ---------------------------------------------------------------------------

@given(crop=_crops, context=snapshot_contexts())
@settings(max_examples=200)
def test_property_2_hard_exclusion(crop, context):
    """For any crop and context where temperature_mean_c > heat_tolerance_ceiling_c + 8,
    the suitability_index must be 0 and hard_exclusion must be True.

    Feature: crop-simulator, Property 2: Hard exclusion zeroes the score
    Validates: Requirements 3.3
    """
    if (
        context.temperature_mean_c is None
        or context.temperature_mean_c <= crop.heat_tolerance_ceiling_c + 8.0
    ):
        # Precondition not met — skip this example
        return

    result = score(crop, context)

    assert result.hard_exclusion is True, (
        f"Expected hard_exclusion=True for crop={crop.name!r} with "
        f"temperature_mean_c={context.temperature_mean_c:.2f} > "
        f"ceiling+8={crop.heat_tolerance_ceiling_c + 8.0:.1f}"
    )
    assert result.suitability_index == 0, (
        f"Expected suitability_index=0 under hard exclusion for crop={crop.name!r}, "
        f"got {result.suitability_index}."
    )
