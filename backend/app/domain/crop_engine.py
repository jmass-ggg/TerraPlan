"""
Crop suitability engine.

Deterministic, snapshot-aware scoring engine that evaluates crop suitability
against environmental conditions and returns explainable component scores.

Public API:
    score(crop, context) -> SimulationResult
    rank_all(context)    -> list[SimulationResult]  (sorted descending)

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.crop_register import CROP_REGISTER, CropRequirements
from app.domain.snapshot_context import SnapshotContext


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ENGINE_VERSION = "farmtwin-crop-v2"

# Weights must sum to 1.0
_WEIGHT_TEMPERATURE = 0.25
_WEIGHT_WATER = 0.30
_WEIGHT_SOIL = 0.20
_WEIGHT_HEAT_SAFETY = 0.10
_WEIGHT_DROUGHT = 0.10
_WEIGHT_ENV = 0.05

# Neutral placeholder used when NDVI is unavailable (documented, not inflating)
_NEUTRAL_NDVI_SCORE = 65.0

# Neutral placeholder scores for soil components when inputs are missing
_NEUTRAL_SOIL_SCORE = 70.0

# Hard exclusion threshold above ceiling (°C)
_HARD_EXCLUSION_MARGIN = 8.0

# Qualitative label thresholds
_GOOD_MATCH_THRESHOLD = 82
_POSSIBLE_MATCH_THRESHOLD = 68


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ComponentScores:
    """Individual 0–100 scores for each scoring dimension."""

    temperature: float | None
    water: float | None
    soil: float | None
    heat_safety: float | None
    drought_flood_safety: float | None
    environmental_condition: float | None


@dataclass(frozen=True)
class SimulationResult:
    """Full result of scoring one crop against one SnapshotContext."""

    crop_name: str
    suitability_index: int | None            # 0–100 weighted sum
    label: str                        # "Good match" | "Possible match" | "Higher caution"
    components: ComponentScores
    limiting_factor: str              # name of the component with lowest contribution
    reason: str                       # plain-language explanation
    hard_exclusion: bool
    hard_exclusion_reason: str | None
    engine_version: str
    snapshot_id: str | None
    data_mode: str
    input_completeness: dict[str, str]   # field → "real" | "demonstration" | "missing"


# ---------------------------------------------------------------------------
# Internal scoring helpers
# ---------------------------------------------------------------------------

def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _range_score(
    value: float | None,
    minimum: float,
    optimum: float,
    maximum: float,
    falloff: float,
) -> float:
    """
    Score a numeric value against a [minimum, optimum, maximum] band.

    Returns 0–100:
      - Exactly at optimum → 100
      - Within [minimum, maximum] → 88–100 (linear falloff from optimum)
      - Outside range → 0–88 (linear decay with given falloff)

    When *value* is None the neutral midpoint score (70) is returned so
    missing data does not silently boost or crush the score beyond what a
    neutral estimate would suggest.
    """
    if value is None:
        return 70.0

    if minimum <= value <= maximum:
        # Distance from optimum within the valid band
        half_range = max((maximum - minimum) / 2, 1.0)
        deviation = abs(value - optimum)
        return _clamp(100.0 - deviation / half_range * 12.0, 88.0, 100.0)

    # Outside the valid band
    distance = (minimum - value) if value < minimum else (value - maximum)
    return _clamp(88.0 - distance / falloff * 88.0, 0.0, 88.0)


def _soil_match(
    ph: float | None,
    clay_pct: float | None,
    crop: CropRequirements,
) -> float:
    """
    0–100 soil compatibility score.

    pH contributes 60 points and texture (clay_pct proxy) contributes 40 points.
    Missing inputs fall back to the neutral placeholder (70).
    """
    if ph is None and clay_pct is None:
        return _NEUTRAL_SOIL_SCORE

    # pH component (0–60)
    if ph is not None:
        if crop.min_soil_ph <= ph <= crop.max_soil_ph:
            mid = (crop.min_soil_ph + crop.max_soil_ph) / 2
            half = max((crop.max_soil_ph - crop.min_soil_ph) / 2, 0.1)
            ph_score = _clamp(60.0 - abs(ph - mid) / half * 8.0, 52.0, 60.0)
        else:
            distance = (crop.min_soil_ph - ph) if ph < crop.min_soil_ph else (ph - crop.max_soil_ph)
            ph_score = _clamp(52.0 - distance / 1.5 * 52.0, 0.0, 52.0)
    else:
        ph_score = 42.0  # neutral fraction of 60

    # Texture component via clay % proxy (0–40)
    # Loamy soils have 15–35% clay — most crops prefer loam, some prefer clay or sandy.
    if clay_pct is not None:
        # Most crops prefer 15–35% clay (loam range); score peaks there.
        # Crops that prefer clay (e.g. Rice) have higher tolerance for high clay.
        # This is a simplified proxy; full texture class logic is post-MVP.
        clay_pref = "clay" in " ".join(crop.soil_texture_preferences).lower()
        sandy_pref = "sandy" in " ".join(crop.soil_texture_preferences).lower()

        if clay_pref and not sandy_pref:
            # Crops that prefer clay: score peaks at higher clay content (30–50%)
            texture_score = _clamp(40.0 - abs(clay_pct - 40.0) / 20.0 * 20.0, 20.0, 40.0)
        elif sandy_pref and not clay_pref:
            # Crops that prefer sandy: score peaks at lower clay content (5–20%)
            texture_score = _clamp(40.0 - abs(clay_pct - 12.0) / 15.0 * 20.0, 20.0, 40.0)
        else:
            # General loam preference: peaks at 20–30% clay
            texture_score = _clamp(40.0 - abs(clay_pct - 25.0) / 20.0 * 20.0, 20.0, 40.0)
    else:
        texture_score = 28.0  # neutral fraction of 40

    return _clamp(ph_score + texture_score, 0.0, 100.0)


def _heat_safety(
    temp: float | None,
    ceiling: float,
) -> float:
    """
    0–100 heat safety score.

    100 when well below ceiling; decays to 0 as temperature approaches
    and exceeds the crop's heat tolerance ceiling.
    """
    if temp is None:
        return 70.0

    if temp <= ceiling - 4:
        return 100.0

    margin = ceiling - temp
    # Linear decay: 0 margin → 0 score; -8 margin → already excluded (hard)
    return _clamp((margin + 4.0) / 8.0 * 100.0, 0.0, 100.0)


def _drought_safety(
    rainfall: float | None,
    vpd: float | None,
    crop: CropRequirements,
) -> float:
    """
    0–100 drought/flood safety score.

    Combines rainfall pressure and VPD (evaporative demand) with the crop's
    drought tolerance.

    - High rainfall relative to crop minimum → low drought pressure.
    - High VPD → higher evaporative demand → more stress for low-tolerance crops.
    - Excess rainfall (beyond crop maximum) → mild flood penalty.

    Missing inputs default to neutral (70).
    """
    if rainfall is None and vpd is None:
        return 70.0

    base_score = 85.0

    # Rainfall pressure
    if rainfall is not None:
        if rainfall < crop.min_rainfall_mm:
            drought_pressure = _clamp(
                (crop.min_rainfall_mm - rainfall) / max(crop.min_rainfall_mm, 1.0),
                0.0,
                1.0,
            )
            # Drought-tolerant crops lose fewer points
            base_score -= drought_pressure * (1.0 - crop.drought_tolerance) * 70.0
        elif rainfall > crop.max_rainfall_mm:
            # Mild flood / waterlogging penalty
            excess = rainfall - crop.max_rainfall_mm
            base_score -= _clamp(excess / max(crop.max_rainfall_mm, 1.0) * 30.0, 0.0, 30.0)

    # VPD penalty (each kPa above 2.5 adds evaporative stress)
    if vpd is not None and vpd > 2.5:
        vpd_pressure = _clamp((vpd - 2.5) / 3.0, 0.0, 1.0)
        base_score -= vpd_pressure * (1.0 - crop.drought_tolerance) * 20.0

    return _clamp(base_score, 0.0, 100.0)


def _ndvi_condition(ndvi: float | None) -> float:
    """
    0–100 environmental condition score from NDVI.

    NDVI range: -1 to 1. Healthy vegetation: ~0.4–0.8.
    Returns neutral 65 when NDVI is unavailable (documented placeholder).
    """
    if ndvi is None:
        return _NEUTRAL_NDVI_SCORE

    # Map NDVI [0, 1] → score [0, 100] with peak at 0.6
    # Below 0 (water, bare soil) → low score; above 0.8 → high score plateau
    if ndvi < 0.0:
        return _clamp(20.0 + ndvi * 20.0, 0.0, 20.0)
    if ndvi <= 0.3:
        # Sparse or stressed vegetation
        return _clamp(20.0 + ndvi / 0.3 * 45.0, 20.0, 65.0)
    if ndvi <= 0.7:
        # Moderate to good vegetation — peak at 0.6
        return _clamp(65.0 + (ndvi - 0.3) / 0.4 * 35.0, 65.0, 100.0)
    # Dense, healthy canopy — plateau near 100
    return 100.0


# ---------------------------------------------------------------------------
# Input completeness helper
# ---------------------------------------------------------------------------

def _build_completeness(context: SnapshotContext) -> dict[str, str]:
    """Map each scored input field to 'real', 'demonstration', or 'missing'."""
    fields = [
        "temperature_mean_c",
        "rainfall_total_mm",
        "soil_ph",
        "soil_clay_pct",
        "soil_sand_pct",
        "ndvi_mean",
        "vpd_kpa",
    ]
    result: dict[str, str] = {}
    for field in fields:
        if field in context.real_input_fields:
            result[field] = "real"
        elif field in context.demonstration_input_fields:
            result[field] = "demonstration"
        else:
            result[field] = "missing"
    return result


# ---------------------------------------------------------------------------
# Label helper
# ---------------------------------------------------------------------------

def _label(suitability_index: int) -> str:
    if suitability_index >= _GOOD_MATCH_THRESHOLD:
        return "Good match"
    if suitability_index >= _POSSIBLE_MATCH_THRESHOLD:
        return "Possible match"
    return "Higher caution"


# ---------------------------------------------------------------------------
# Limiting factor and reason
# ---------------------------------------------------------------------------

_COMPONENT_NAMES = {
    "temperature": "temperature",
    "water": "water / rainfall",
    "soil": "soil compatibility",
    "heat_safety": "heat safety",
    "drought_flood_safety": "drought / flood safety",
    "environmental_condition": "environmental condition",
}


def _limiting_factor(components: ComponentScores, weights: dict[str, float]) -> str:
    """Return the name of the component with the highest negative impact."""
    losses = {key: (100 - getattr(components, key)) * weight
              for key, weight in weights.items() if getattr(components, key) is not None}
    return _COMPONENT_NAMES[max(losses, key=losses.get)] if losses else "missing evidence"


def _build_reason(
    crop_name: str,
    suitability_index: int,
    label: str,
    limiting: str,
    hard_exclusion: bool,
    hard_exclusion_reason: str | None,
) -> str:
    if hard_exclusion:
        return f"{crop_name} is excluded: {hard_exclusion_reason}."
    if suitability_index >= _GOOD_MATCH_THRESHOLD:
        return (
            f"{crop_name} is a good match for these conditions. "
            f"The main constraint is {limiting}."
        )
    return (
        f"{crop_name} scores {suitability_index}/100 ({label.lower()}). "
        f"The primary limiting factor is {limiting}."
    )


# ---------------------------------------------------------------------------
# Public: score()
# ---------------------------------------------------------------------------

def score(crop: CropRequirements, context: SnapshotContext) -> SimulationResult:
    """
    Deterministically score *crop* against *context*.

    Identical inputs always produce identical output (Property 1).
    Hard exclusion returns suitability_index=0 regardless of other scores (Property 2).
    All component scores are in [0, 100] and the weighted sum is in [0, 100] (Property 3).

    Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7
    """
    weights = {
        "temperature": _WEIGHT_TEMPERATURE,
        "water": _WEIGHT_WATER,
        "soil": _WEIGHT_SOIL,
        "heat_safety": _WEIGHT_HEAT_SAFETY,
        "drought_flood_safety": _WEIGHT_DROUGHT,
        "environmental_condition": _WEIGHT_ENV,
    }

    # --- Hard exclusion check (Requirement 3.3) ---
    hard_exclusion = False
    hard_exclusion_reason: str | None = None
    if (
        context.temperature_mean_c is not None
        and context.temperature_mean_c > crop.heat_tolerance_ceiling_c + _HARD_EXCLUSION_MARGIN
    ):
        hard_exclusion = True
        hard_exclusion_reason = (
            f"Temperature {context.temperature_mean_c:.1f}°C exceeds the lethal "
            f"threshold for {crop.name} ({crop.heat_tolerance_ceiling_c + _HARD_EXCLUSION_MARGIN:.0f}°C)."
        )

    # --- Component scores (Requirements 3.1, 3.2) ---
    temp_score = _range_score(
        context.temperature_mean_c,
        crop.min_temp_c,
        crop.optimum_temp_c,
        crop.max_temp_c,
        falloff=12.0,
    )
    water_score = _range_score(
        context.rainfall_total_mm + context.irrigation_total_mm if context.rainfall_total_mm is not None else None,
        crop.min_rainfall_mm,
        (crop.min_rainfall_mm + crop.max_rainfall_mm) / 2,   # optimum = midpoint of valid range
        crop.max_rainfall_mm,
        falloff=100.0,
    )
    soil_score = _soil_match(context.soil_ph, context.soil_clay_pct, crop)
    heat_score = _heat_safety(context.temperature_mean_c, crop.heat_tolerance_ceiling_c)
    drought_score = _drought_safety(context.rainfall_total_mm + context.irrigation_total_mm if context.rainfall_total_mm is not None else None, context.vpd_kpa, crop)
    env_score = _ndvi_condition(context.ndvi_mean)

    components = ComponentScores(
        temperature=_clamp(temp_score, 0.0, 100.0) if context.temperature_mean_c is not None else None,
        water=_clamp(water_score, 0.0, 100.0) if context.rainfall_total_mm is not None else None,
        soil=_clamp(soil_score, 0.0, 100.0) if context.soil_ph is not None and context.soil_clay_pct is not None else None,
        heat_safety=_clamp(heat_score, 0.0, 100.0) if context.temperature_mean_c is not None else None,
        drought_flood_safety=_clamp(drought_score, 0.0, 100.0) if context.rainfall_total_mm is not None else None,
        environmental_condition=_clamp(env_score, 0.0, 100.0) if context.ndvi_mean is not None else None,
    )

    supported = context.temperature_mean_c is not None and context.rainfall_total_mm is not None
    available = {key: weight for key, weight in weights.items() if getattr(components, key) is not None}
    suitability_index = None
    if hard_exclusion:
        # Hard exclusion always forces suitability_index to 0, regardless of missing inputs
        suitability_index = 0
    elif supported:
        raw = sum(getattr(components, key) * weight for key, weight in available.items()) / sum(available.values())
        suitability_index = int(_clamp(round(raw), 0, 100))
    limiting = _limiting_factor(components, weights)
    qual_label = _label(suitability_index) if suitability_index is not None else "Insufficient evidence"
    reason = (_build_reason(crop.name, suitability_index, qual_label, limiting, hard_exclusion, hard_exclusion_reason)
              if suitability_index is not None else "A growing-period temperature and rainfall baseline is required before scoring this crop.")
    if supported and len(available) < len(weights):
        reason += " Partial assessment: unavailable components are excluded and remaining weights are normalized."
    completeness = _build_completeness(context)

    return SimulationResult(
        crop_name=crop.name,
        suitability_index=suitability_index,
        label=qual_label,
        components=components,
        limiting_factor=limiting,
        reason=reason,
        hard_exclusion=hard_exclusion,
        hard_exclusion_reason=hard_exclusion_reason,
        engine_version=ENGINE_VERSION,
        snapshot_id=context.snapshot_id,
        data_mode=context.data_mode,
        input_completeness=completeness,
    )


# ---------------------------------------------------------------------------
# Public: rank_all()
# ---------------------------------------------------------------------------

def rank_all(context: SnapshotContext) -> list[SimulationResult]:
    """
    Score every crop in CROP_REGISTER and return them sorted by
    suitability_index descending (ties broken alphabetically by crop name).

    Returns exactly len(CROP_REGISTER) results (Property 4).

    Requirements: 4.1, 4.4
    """
    results = [score(crop, context) for crop in CROP_REGISTER]
    results.sort(key=lambda r: (-(r.suitability_index if r.suitability_index is not None else -1), r.crop_name))
    return results
