"""
Risk engine for the Farm Risk Center.

Assesses five hazards (Drought, Heat, Heavy_Rainfall, Flood_Exposure, Wind)
from a SnapshotContext and returns a RiskResponse with all assessments and
resolved action recommendations.

Requirements: 1.3, 2.1–2.6, 3.1–3.6, 4.1–4.5, 5.1–5.5, 6.1–6.5, 7.1–7.3
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.domain.crop_register import CROP_REGISTER
from app.domain.risk_rules import ACTION_RULES, ActionRule
from app.domain.snapshot_context import SnapshotContext

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ENGINE_VERSION = "farmtwin-risk-v1"

# Hazard level constants
LEVEL_LOW = "Low"
LEVEL_MEDIUM = "Medium"
LEVEL_HIGH = "High"
LEVEL_UNKNOWN = "Unknown"

# Drought thresholds (rainfall ratio vs seasonal baseline)
DROUGHT_HIGH_RATIO = 0.60   # below 60% of baseline → High
DROUGHT_MEDIUM_RATIO = 0.80  # below 80% of baseline → Medium; else Low

# Heat thresholds (°C)
HEAT_HIGH_TEMP = 33.0
HEAT_MEDIUM_TEMP = 30.0

# At-risk crop window (°C below heat_tolerance_ceiling_c)
HEAT_AT_RISK_WINDOW_C = 3.0
HEAT_MAX_AT_RISK_CROPS = 3

# A current NDVI below this value indicates sparse vegetation. It is not a
# historical trend and must never be described as an NDVI decline.
LOW_NDVI_THRESHOLD = 0.3

# Heavy rainfall thresholds (mm over 7 days)
HEAVY_RAIN_HIGH_MM = 120.0
HEAVY_RAIN_MEDIUM_MM = 60.0

# Flood thresholds (slope %)
FLOOD_HIGH_SLOPE_PCT = 2.0   # slope < 2% + heavy rain Medium/High → High
FLOOD_MEDIUM_SLOPE_PCT = 5.0  # slope < 5% + heavy rain High → Medium

# Wind thresholds (m/s)
WIND_HIGH_MS = 15.0
WIND_MEDIUM_MS = 8.0


# ---------------------------------------------------------------------------
# HazardAssessment and RiskResponse dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HazardAssessment:
    """
    Immutable assessment for a single hazard type.

    Requirements: 1.3, 1.4, 1.5
    """

    hazard: str                          # drought | heat | heavy_rainfall | flood_exposure | wind
    index: int                           # 0–100
    level: str                           # Low | Medium | High | Unknown
    driver: str                          # primary driver name
    explanation: str                     # plain-language sentence
    horizon: str                         # current | short_term | seasonal
    at_risk_crops: tuple[str, ...]       # heat only; empty otherwise
    actions: tuple[ActionRule, ...]      # resolved action recommendations
    evidence_used: dict[str, str]        # field → "real" | "demonstration" | "missing"
    engine_version: str
    snapshot_id: str | None
    data_mode: str


@dataclass(frozen=True)
class RiskResponse:
    """
    Aggregated risk response with all 5 hazard assessments.

    Requirements: 1.3, 8.2
    """

    farm_id: str
    assessments: tuple[HazardAssessment, ...]   # always 5
    engine_version: str
    snapshot_id: str | None
    data_mode: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clamp(value: float, low: float, high: float) -> int:
    """Clamp value to [low, high] and return as int."""
    return int(max(low, min(high, round(value))))


def _evidence_status(context: SnapshotContext, field_name: str) -> str:
    """Return 'real', 'demonstration', or 'missing' for a field in context."""
    if field_name in context.real_input_fields:
        return "real"
    if field_name in context.demonstration_input_fields:
        return "demonstration"
    return "missing"


def _extract_wind_max(context: SnapshotContext) -> float | None:
    """
    Extract maximum wind speed from the SnapshotContext.

    Checks conduit wind_spd_max_ms first (preferred for current/recent),
    then falls back to weather wind_speed_10m.

    Requirements: 6.1
    """
    return getattr(context, "wind_max_ms", None)


def _extract_rain_7d(context: SnapshotContext) -> float | None:
    """
    Extract 7-day cumulative rainfall.

    Uses the dedicated rain_7d_mm field if present on context,
    otherwise uses the rainfall_total_mm (for demonstration contexts).

    Requirements: 4.1
    """
    return getattr(context, "rain_7d_mm", None)


def _extract_slope_pct(context: SnapshotContext) -> float | None:
    """
    Extract mean slope in percent from the SnapshotContext.

    Terrain provider records slope in degrees; convert to percent:
    slope_pct = tan(slope_deg * pi/180) * 100

    Requirements: 5.1
    """
    return getattr(context, "slope_pct", None)


def _extract_climate_baseline_rainfall(context: SnapshotContext) -> float | None:
    """
    Return the seasonal climate baseline rainfall (mm) for drought ratio.

    For demonstration contexts this is approximated from rainfall_total_mm
    with a neutral ratio (i.e. the demo value is treated as baseline).

    Requirements: 2.1
    """
    return getattr(context, "climate_baseline_rainfall_mm", None)


# ---------------------------------------------------------------------------
# Individual hazard assessors
# ---------------------------------------------------------------------------

def _assess_drought(context: SnapshotContext) -> HazardAssessment:
    """
    Assess drought / water stress hazard.

    Requirements: 2.1–2.6
    """
    rainfall = context.rainfall_total_mm
    baseline = _extract_climate_baseline_rainfall(context)

    evidence_used = {
        "rainfall_total_mm": _evidence_status(context, "rainfall_total_mm"),
        "vpd_kpa": _evidence_status(context, "vpd_kpa"),
        "ndvi_mean": _evidence_status(context, "ndvi_mean"),
    }

    # Requirement 2.5: missing rainfall → Unknown
    if rainfall is None or baseline is None or baseline <= 0:
        return HazardAssessment(
            hazard="drought",
            index=0,
            level=LEVEL_UNKNOWN,
            driver="rainfall_deficit",
            explanation="Matched rainfall and baseline data are required; drought assessment cannot be completed.",
            horizon="short_term",
            at_risk_crops=(),
            actions=(),
            evidence_used=evidence_used,
            engine_version=ENGINE_VERSION,
            snapshot_id=context.snapshot_id,
            data_mode=context.data_mode,
        )

    ratio = rainfall / baseline

    drought_index = _clamp((1.0 - ratio) * 100, 0, 100)

    if ratio < DROUGHT_HIGH_RATIO:
        level = LEVEL_HIGH
    elif ratio < DROUGHT_MEDIUM_RATIO:
        level = LEVEL_MEDIUM
    else:
        level = LEVEL_LOW

    # Determine primary driver
    vpd = context.vpd_kpa
    ndvi = context.ndvi_mean

    if vpd is not None and vpd > 2.5 and ndvi is not None and ndvi < LOW_NDVI_THRESHOLD:
        driver = "combined"
    elif vpd is not None and vpd > 2.5:
        driver = "vpd_stress"
    elif ndvi is not None and ndvi < LOW_NDVI_THRESHOLD:
        driver = "low_ndvi"
    else:
        driver = "rainfall_deficit"

    if ratio >= 1.0:
        surplus_pct = (ratio - 1.0) * 100.0
        explanation = (
            f"Rainfall over the assessment period was {rainfall:.1f} mm compared with "
            f"a {baseline:.1f} mm climatological baseline. Rainfall was "
            f"{surplus_pct:.0f}% above baseline, so there is no rainfall-deficit "
            "drought signal."
        )
    else:
        deficit_pct = (1.0 - ratio) * 100.0
        explanation = (
            f"Rainfall over the assessment period was {rainfall:.1f} mm compared with "
            f"a {baseline:.1f} mm climatological baseline, representing a "
            f"{deficit_pct:.0f}% rainfall deficit. The resulting drought level is {level.lower()}."
        )

    if driver != "rainfall_deficit":
        explanation += f" Additional stress evidence: {driver.replace('_', ' ')}."

    return HazardAssessment(
        hazard="drought",
        index=drought_index,
        level=level,
        driver=driver,
        explanation=explanation,
        horizon="short_term",
        at_risk_crops=(),
        actions=(),
        evidence_used=evidence_used,
        engine_version=ENGINE_VERSION,
        snapshot_id=context.snapshot_id,
        data_mode=context.data_mode,
    )


def _heat_at_risk_crops(temp_mean: float) -> tuple[str, ...]:
    """
    Return up to 3 crop names from CROP_REGISTER whose heat_tolerance_ceiling_c
    is within HEAT_AT_RISK_WINDOW_C of the current mean temperature.

    Requirements: 3.5
    """
    at_risk = [
        crop.name
        for crop in CROP_REGISTER
        if abs(crop.heat_tolerance_ceiling_c - temp_mean) <= HEAT_AT_RISK_WINDOW_C
    ]
    return tuple(at_risk[:HEAT_MAX_AT_RISK_CROPS])


def _assess_heat(context: SnapshotContext) -> HazardAssessment:
    """
    Assess heat stress hazard.

    Requirements: 3.1–3.6
    """
    temp = context.temperature_mean_c

    evidence_used = {
        "temperature_mean_c": _evidence_status(context, "temperature_mean_c"),
    }

    # Requirement 3.6: missing temperature → Unknown
    if temp is None:
        return HazardAssessment(
            hazard="heat",
            index=0,
            level=LEVEL_UNKNOWN,
            driver="temperature",
            explanation="Temperature data is unavailable; heat assessment cannot be completed.",
            horizon="current",
            at_risk_crops=(),
            actions=(),
            evidence_used=evidence_used,
            engine_version=ENGINE_VERSION,
            snapshot_id=context.snapshot_id,
            data_mode=context.data_mode,
        )

    # Requirement 3.1: Heat_Index from mean temperature relative to seasonal range
    # heat_index = clamp(round((temp_mean - 20) / 15 * 100), 0, 100)
    heat_index = _clamp((temp - 20.0) / 15.0 * 100.0, 0, 100)

    if temp > HEAT_HIGH_TEMP:
        level = LEVEL_HIGH
    elif temp > HEAT_MEDIUM_TEMP:
        level = LEVEL_MEDIUM
    else:
        level = LEVEL_LOW

    at_risk = _heat_at_risk_crops(temp)

    crop_note = ""
    if at_risk:
        crop_note = f" Crops near tolerance ceiling: {', '.join(at_risk)}."

    explanation = (
        f"Mean temperature is {temp:.1f}°C "
        f"(High >33°C, Medium 30–33°C, Low <30°C).{crop_note}"
    )

    return HazardAssessment(
        hazard="heat",
        index=heat_index,
        level=level,
        driver="temperature",
        explanation=explanation,
        horizon="current",
        at_risk_crops=at_risk,
        actions=(),
        evidence_used=evidence_used,
        engine_version=ENGINE_VERSION,
        snapshot_id=context.snapshot_id,
        data_mode=context.data_mode,
    )


def _assess_heavy_rainfall(context: SnapshotContext) -> HazardAssessment:
    """
    Assess heavy rainfall hazard from 7-day cumulative rainfall.

    Requirements: 4.1–4.6
    """
    rain_7d = _extract_rain_7d(context)

    evidence_used = {
        "rain_7d_mm": "real" if rain_7d is not None and context.source == "snapshot" else
                      "demonstration" if rain_7d is not None else "missing",
    }

    # Requirement 4.5: missing 7-day rainfall → Unknown
    if rain_7d is None:
        return HazardAssessment(
            hazard="heavy_rainfall",
            index=0,
            level=LEVEL_UNKNOWN,
            driver="rainfall_intensity",
            explanation="7-day rainfall data is unavailable; heavy rainfall assessment cannot be completed.",
            horizon="short_term",
            at_risk_crops=(),
            actions=(),
            evidence_used=evidence_used,
            engine_version=ENGINE_VERSION,
            snapshot_id=context.snapshot_id,
            data_mode=context.data_mode,
        )

    heavy_index = _clamp(rain_7d / HEAVY_RAIN_HIGH_MM * 100.0, 0, 100)

    if rain_7d > HEAVY_RAIN_HIGH_MM:
        level = LEVEL_HIGH
    elif rain_7d > HEAVY_RAIN_MEDIUM_MM:
        level = LEVEL_MEDIUM
    else:
        level = LEVEL_LOW

    # Requirement 4.6: crop risks note for Medium/High
    crop_risk_note = ""
    if level in (LEVEL_MEDIUM, LEVEL_HIGH):
        crop_risk_note = (
            " Relevant crop risks: waterlogging, fungal disease pressure, and harvest delays."
        )

    explanation = (
        f"7-day cumulative rainfall is {rain_7d:.0f} mm "
        f"(High >120 mm, Medium 60–120 mm, Low <60 mm).{crop_risk_note}"
    )

    return HazardAssessment(
        hazard="heavy_rainfall",
        index=heavy_index,
        level=level,
        driver="rainfall_intensity",
        explanation=explanation,
        horizon="short_term",
        at_risk_crops=(),
        actions=(),
        evidence_used=evidence_used,
        engine_version=ENGINE_VERSION,
        snapshot_id=context.snapshot_id,
        data_mode=context.data_mode,
    )


def _assess_flood(
    context: SnapshotContext,
    heavy_rainfall_level: str,
) -> HazardAssessment:
    """
    Assess flood exposure from terrain slope and heavy rainfall level.

    Requirements: 5.1–5.5
    """
    slope_pct = _extract_slope_pct(context)

    evidence_used = {
        "slope_pct": "real" if slope_pct is not None and context.source == "snapshot" else
                     "missing" if slope_pct is None else "demonstration",
        "heavy_rainfall_level": "derived",
    }

    # Requirement 5.2: terrain unavailable → Unknown
    if slope_pct is None:
        return HazardAssessment(
            hazard="flood_exposure",
            index=0,
            level=LEVEL_UNKNOWN,
            driver="terrain_slope",
            explanation=(
                "Terrain evidence (slope, elevation) is required for flood exposure "
                "assessment but is unavailable for this farm."
            ),
            horizon="short_term",
            at_risk_crops=(),
            actions=(),
            evidence_used=evidence_used,
            engine_version=ENGINE_VERSION,
            snapshot_id=context.snapshot_id,
            data_mode=context.data_mode,
        )

    # Requirement 5.4: low rainfall → Low regardless of terrain
    if heavy_rainfall_level == LEVEL_LOW:
        flood_index = _clamp(slope_pct * 2.0, 0, 30)
        explanation = (
            f"Terrain slope is {slope_pct:.1f}% but current 7-day rainfall is low, "
            "so flood exposure is low. This is a terrain-and-rainfall exposure index, "
            "not a flood probability."
        )
        return HazardAssessment(
            hazard="flood_exposure",
            index=flood_index,
            level=LEVEL_LOW,
            driver="terrain_slope",
            explanation=explanation,
            horizon="short_term",
            at_risk_crops=(),
            actions=(),
            evidence_used=evidence_used,
            engine_version=ENGINE_VERSION,
            snapshot_id=context.snapshot_id,
            data_mode=context.data_mode,
        )

    # Requirement 5.3: slope < 2% + Medium or High rainfall → High
    if slope_pct < FLOOD_HIGH_SLOPE_PCT and heavy_rainfall_level in (LEVEL_MEDIUM, LEVEL_HIGH):
        flood_index = 85
        level = LEVEL_HIGH
        explanation = (
            f"Flat terrain (slope {slope_pct:.1f}%) combined with "
            f"{heavy_rainfall_level.lower()} rainfall creates high flood exposure. "
            "This is a terrain-and-rainfall exposure index, not a flood probability."
        )
    elif slope_pct < FLOOD_MEDIUM_SLOPE_PCT and heavy_rainfall_level == LEVEL_HIGH:
        flood_index = 60
        level = LEVEL_MEDIUM
        explanation = (
            f"Gentle terrain (slope {slope_pct:.1f}%) combined with "
            f"high rainfall creates moderate flood exposure. "
            "This is a terrain-and-rainfall exposure index, not a flood probability."
        )
    else:
        flood_index = _clamp(slope_pct * 3.0, 0, 40)
        level = LEVEL_LOW
        explanation = (
            f"Terrain slope is {slope_pct:.1f}%; flood exposure is low given current "
            "slope and rainfall combination. "
            "This is a terrain-and-rainfall exposure index, not a flood probability."
        )

    return HazardAssessment(
        hazard="flood_exposure",
        index=flood_index,
        level=level,
        driver="terrain_slope",
        explanation=explanation,
        horizon="short_term",
        at_risk_crops=(),
        actions=(),
        evidence_used=evidence_used,
        engine_version=ENGINE_VERSION,
        snapshot_id=context.snapshot_id,
        data_mode=context.data_mode,
    )


def _assess_wind(context: SnapshotContext) -> HazardAssessment:
    """
    Assess wind hazard from maximum wind speed.

    Requirements: 6.1–6.5
    """
    wind_max = _extract_wind_max(context)

    evidence_used = {
        "wind_max_ms": "real" if wind_max is not None and context.source == "snapshot" else
                       "demonstration" if wind_max is not None else "missing",
    }

    # Requirement 6.5: wind data unavailable → Unknown
    if wind_max is None:
        return HazardAssessment(
            hazard="wind",
            index=0,
            level=LEVEL_UNKNOWN,
            driver="wind_speed",
            explanation="Wind speed data is unavailable; wind assessment cannot be completed.",
            horizon="short_term",
            at_risk_crops=(),
            actions=(),
            evidence_used=evidence_used,
            engine_version=ENGINE_VERSION,
            snapshot_id=context.snapshot_id,
            data_mode=context.data_mode,
        )

    wind_index = _clamp(wind_max / 20.0 * 100.0, 0, 100)

    if wind_max > WIND_HIGH_MS:
        level = LEVEL_HIGH
    elif wind_max > WIND_MEDIUM_MS:
        level = LEVEL_MEDIUM
    else:
        level = LEVEL_LOW

    explanation = (
        f"Maximum wind speed is {wind_max:.1f} m/s "
        f"(High >15 m/s, Medium 8–15 m/s, Low <8 m/s)."
    )

    return HazardAssessment(
        hazard="wind",
        index=wind_index,
        level=level,
        driver="wind_speed",
        explanation=explanation,
        horizon="short_term",
        at_risk_crops=(),
        actions=(),
        evidence_used=evidence_used,
        engine_version=ENGINE_VERSION,
        snapshot_id=context.snapshot_id,
        data_mode=context.data_mode,
    )


# ---------------------------------------------------------------------------
# Action resolution
# ---------------------------------------------------------------------------

def _resolve_actions(
    assessments: list[HazardAssessment],
) -> dict[str, tuple[ActionRule, ...]]:
    """
    Resolve ACTION_RULES for each hazard based on computed levels.

    Filter rules to those whose hazard matches and applicable_levels contains
    the computed level (lower-cased). Then remove conflicted rules:
    if rule A conflicts with rule B, and both are active, remove the lower
    priority rule (higher priority number = lower priority).

    Returns a dict mapping hazard name → tuple of resolved ActionRules.

    Requirements: 7.1, 7.3
    """
    level_map = {a.hazard: a.level for a in assessments}

    # Collect candidate rules per hazard
    active_rules: list[ActionRule] = []
    for rule in ACTION_RULES:
        hazard_level = level_map.get(rule.hazard, LEVEL_UNKNOWN)
        if hazard_level.lower() in rule.applicable_levels:
            active_rules.append(rule)

    # Build a lookup of active rule IDs and their assessment indices
    active_ids = {r.id for r in active_rules}
    index_map = {a.hazard: a.index for a in assessments}

    # Remove conflicted rules.
    # A rule listed in another active rule's conflict_with is a candidate for
    # removal.  When both rules have identical priority (mutual conflict), the
    # rule whose hazard has the higher current index is considered more urgent
    # and wins — its conflict target is removed.  When priorities differ, the
    # lower-priority (higher number) rule is always removed.
    removed_ids: set[str] = set()

    # Process rules ordered by descending hazard index so the most severe
    # assessment wins when priorities are equal.
    active_rules_sorted = sorted(
        active_rules,
        key=lambda r: (-index_map.get(r.hazard, 0), r.priority),
    )

    for rule in active_rules_sorted:
        if rule.id in removed_ids:
            continue
        for conflict_id in rule.conflict_with:
            if conflict_id in active_ids and conflict_id not in removed_ids:
                conflicting = next(
                    (r for r in active_rules if r.id == conflict_id), None
                )
                if conflicting is not None:
                    if rule.priority < conflicting.priority:
                        # This rule has higher priority — remove the target
                        removed_ids.add(conflict_id)
                    elif rule.priority > conflicting.priority:
                        # This rule has lower priority — remove itself
                        removed_ids.add(rule.id)
                        break
                    else:
                        # Equal priority — the rule with the higher hazard index
                        # wins (already sorted that way), so remove the target
                        removed_ids.add(conflict_id)

    final_rules = [r for r in active_rules if r.id not in removed_ids]

    # Group by hazard
    result: dict[str, list[ActionRule]] = {}
    for rule in final_rules:
        result.setdefault(rule.hazard, []).append(rule)

    return {hazard: tuple(rules) for hazard, rules in result.items()}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def assess_all(context: SnapshotContext, farm_id: str) -> RiskResponse:
    """
    Assess all 5 hazards from the given SnapshotContext.

    Flood assessment depends on heavy_rainfall level so heavy_rainfall is
    computed first and its level passed to _assess_flood.

    Requirements: 1.3, 2.1–2.6, 3.1–3.6, 4.1–4.5, 5.1–5.5, 6.1–6.5, 7.1–7.3
    """
    drought = _assess_drought(context)
    heat = _assess_heat(context)
    heavy_rainfall = _assess_heavy_rainfall(context)
    flood = _assess_flood(context, heavy_rainfall.level)
    wind = _assess_wind(context)

    raw_assessments = [drought, heat, heavy_rainfall, flood, wind]

    # Resolve actions for all assessments together (conflict resolution)
    action_map = _resolve_actions(raw_assessments)

    # Attach resolved actions to each assessment
    final_assessments: list[HazardAssessment] = []
    for assessment in raw_assessments:
        resolved = action_map.get(assessment.hazard, ())
        # Replace the empty actions tuple with resolved ones
        final_assessments.append(
            HazardAssessment(
                hazard=assessment.hazard,
                index=assessment.index,
                level=assessment.level,
                driver=assessment.driver,
                explanation=assessment.explanation,
                horizon=assessment.horizon,
                at_risk_crops=assessment.at_risk_crops,
                actions=resolved,
                evidence_used=assessment.evidence_used,
                engine_version=assessment.engine_version,
                snapshot_id=assessment.snapshot_id,
                data_mode=assessment.data_mode,
            )
        )

    return RiskResponse(
        farm_id=farm_id,
        assessments=tuple(final_assessments),
        engine_version=ENGINE_VERSION,
        snapshot_id=context.snapshot_id,
        data_mode=context.data_mode,
    )
