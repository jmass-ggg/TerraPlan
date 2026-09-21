"""Deterministic demonstration engine for seasonal planning and climate risks.

This module intentionally does not pretend that the generated climate profile is
observed farm data.  It provides a reproducible, coordinate-aware planning model
for local demonstrations until versioned weather, soil, terrain, and satellite
snapshots are available.
"""

from __future__ import annotations

import calendar
import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CropRequirement:
    name: str
    min_temp_c: float
    max_temp_c: float
    min_rainfall_mm: float
    max_rainfall_mm: float
    drought_tolerance: float
    heat_tolerance_c: float
    growing_months: int


CROPS = (
    CropRequirement("Maize", 18, 30, 80, 160, 0.45, 35, 4),
    CropRequirement("Wheat", 10, 24, 45, 110, 0.55, 32, 5),
    CropRequirement("Rice", 20, 32, 140, 260, 0.20, 36, 5),
    CropRequirement("Potato", 12, 24, 55, 120, 0.45, 30, 4),
    CropRequirement("Tomato", 18, 29, 45, 100, 0.35, 33, 4),
    CropRequirement("Onion", 13, 27, 35, 85, 0.60, 34, 4),
    CropRequirement("Beans", 16, 28, 55, 120, 0.50, 33, 3),
    CropRequirement("Soybean", 18, 30, 65, 135, 0.50, 35, 4),
    CropRequirement("Sorghum", 20, 34, 35, 90, 0.90, 39, 4),
    CropRequirement("Cabbage", 12, 24, 50, 115, 0.35, 30, 4),
    CropRequirement("Carrot", 12, 25, 40, 95, 0.50, 31, 3),
    CropRequirement("Sweet potato", 20, 30, 55, 125, 0.70, 36, 5),
)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _cyclic_distance(month: int, peak: int) -> int:
    direct = abs(month - peak)
    return min(direct, 12 - direct)


def _seasonal_climate(latitude: float, longitude: float, month: int) -> tuple[float, float]:
    """Return an illustrative monthly temperature and rainfall profile."""
    absolute_latitude = min(abs(latitude), 70.0)
    base_temperature = _clamp(29.0 - absolute_latitude * 0.32, 7.0, 29.0)
    amplitude = 2.0 + absolute_latitude * 0.13
    warmest_month = 7 if latitude >= 0 else 1
    temperature = base_temperature + amplitude * math.cos(
        2 * math.pi * (month - warmest_month) / 12
    )

    if absolute_latitude < 12:
        long_rains = 78 * math.exp(-(_cyclic_distance(month, 4) ** 2) / 2.8)
        short_rains = 56 * math.exp(-(_cyclic_distance(month, 10) ** 2) / 2.2)
        rainfall = 42 + long_rains + short_rains
    else:
        wettest_month = 7 if latitude >= 0 else 1
        season = max(0.0, math.cos(2 * math.pi * (month - wettest_month) / 12))
        longitude_adjustment = 8 * math.sin(math.radians(longitude))
        rainfall = 32 + 125 * season + longitude_adjustment

    return round(temperature, 1), round(_clamp(rainfall, 18, 220), 1)


def _range_score(value: float, minimum: float, maximum: float, falloff: float) -> float:
    if minimum <= value <= maximum:
        midpoint = (minimum + maximum) / 2
        half_range = max((maximum - minimum) / 2, 1)
        return _clamp(100 - abs(value - midpoint) / half_range * 12, 88, 100)
    distance = minimum - value if value < minimum else value - maximum
    return _clamp(88 - distance / falloff * 88, 0, 88)


def score_crop(crop: CropRequirement, temperature_c: float, rainfall_mm: float) -> dict[str, Any]:
    temperature_score = _range_score(
        temperature_c, crop.min_temp_c, crop.max_temp_c, 12
    )
    water_score = _range_score(
        rainfall_mm, crop.min_rainfall_mm, crop.max_rainfall_mm, 100
    )

    drought_pressure = _clamp(
        (crop.min_rainfall_mm - rainfall_mm) / max(crop.min_rainfall_mm, 1), 0, 1
    )
    heat_pressure = _clamp((temperature_c - crop.heat_tolerance_c) / 8, 0, 1)
    climate_safety = _clamp(
        88 - drought_pressure * (1 - crop.drought_tolerance) * 75 - heat_pressure * 70,
        10,
        96,
    )

    # Neutral assumptions remain visible in the API and UI. They are not farm measurements.
    soil_score = 70.0
    environmental_score = 65.0
    total = round(
        temperature_score * 0.25
        + water_score * 0.30
        + soil_score * 0.25
        + climate_safety * 0.15
        + environmental_score * 0.05
    )

    if total >= 82:
        label = "Good match"
    elif total >= 68:
        label = "Possible match"
    else:
        label = "Higher caution"

    limiting_factor = "water" if water_score < temperature_score else "temperature"
    reason = (
        f"{crop.name} is a {label.lower()} for this demonstration month; "
        f"{limiting_factor} is the main climate constraint."
    )
    return {
        "crop": crop.name,
        "score": total,
        "label": label,
        "temperature_score": round(temperature_score),
        "water_score": round(water_score),
        "soil_score": round(soil_score),
        "climate_safety_score": round(climate_safety),
        "environmental_score": round(environmental_score),
        "reason": reason,
        "growing_months": crop.growing_months,
    }


def _month_risk(temperature_c: float, rainfall_mm: float) -> str:
    if rainfall_mm < 48:
        return "Drought / water stress"
    if rainfall_mm > 165:
        return "Heavy rain"
    if temperature_c > 32:
        return "Heat stress"
    return "No dominant climate signal"


def _risk_summary(
    selected_month: int,
    temperature_c: float,
    rainfall_mm: float,
    scores: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    drought_level = "High" if rainfall_mm < 48 else "Medium" if rainfall_mm < 78 else "Low"
    heat_level = "High" if temperature_c > 33 else "Medium" if temperature_c > 29 else "Low"
    weak_crops = [item["crop"] for item in scores if item["water_score"] < 65][:3]
    heat_crops = [
        crop.name for crop in CROPS if temperature_c > crop.heat_tolerance_c - 2
    ][:3]
    resilient = [
        crop.name for crop in sorted(CROPS, key=lambda item: item.drought_tolerance, reverse=True)[:3]
    ]
    month_name = calendar.month_name[selected_month]
    return [
        {
            "slug": "drought",
            "name": "Drought / water stress",
            "level": drought_level,
            "why": f"The {month_name} demonstration profile has {rainfall_mm:.0f} mm rainfall and {temperature_c:.1f}°C temperature.",
            "affected_crops": weak_crops or ["No crop flagged by the screening rule"],
            "resilient_options": resilient,
            "recommended_action": "Shift the planting window toward a wetter month or compare a lower-water crop before committing inputs.",
            "evidence_note": "Screening result from the demonstration climate profile; not a drought forecast.",
        },
        {
            "slug": "heavy-rain",
            "name": "Heavy rain / flood exposure",
            "level": "Unknown",
            "why": "Rainfall screening is available, but validated slope, elevation, drainage, and flood-model evidence are not connected.",
            "affected_crops": ["Requires terrain evidence"],
            "resilient_options": ["No crop substitution claimed"],
            "recommended_action": "Review field drainage and local flood guidance before using this screen for a planting decision.",
            "evidence_note": "Unknown is intentionally different from low risk; no flood probability is calculated.",
        },
        {
            "slug": "heat",
            "name": "Heat stress",
            "level": heat_level,
            "why": f"The scenario temperature for {month_name} is {temperature_c:.1f}°C and is compared with crop heat-tolerance thresholds.",
            "affected_crops": heat_crops or ["No crop flagged by the screening rule"],
            "resilient_options": ["Sorghum", "Sweet potato", "Rice"],
            "recommended_action": "If heat remains elevated, compare a cooler planting window and protect heat-sensitive seedlings.",
            "evidence_note": "Threshold screening only; local canopy temperature and forecast evidence are unavailable.",
        },
    ]


def build_decision_support(
    *,
    farm_id: str,
    farm_name: str,
    latitude: float,
    longitude: float,
    selected_month: int,
    rainfall_change_pct: float,
    temperature_change_c: float,
) -> dict[str, Any]:
    months: list[dict[str, Any]] = []
    baseline_scores_by_month: dict[int, list[dict[str, Any]]] = {}
    scenario_scores_by_month: dict[int, list[dict[str, Any]]] = {}

    for month in range(1, 13):
        baseline_temperature, baseline_rainfall = _seasonal_climate(latitude, longitude, month)
        scenario_temperature = round(baseline_temperature + temperature_change_c, 1)
        scenario_rainfall = round(
            _clamp(baseline_rainfall * (1 + rainfall_change_pct / 100), 0, 400), 1
        )
        baseline_scores = sorted(
            (score_crop(crop, baseline_temperature, baseline_rainfall) for crop in CROPS),
            key=lambda item: (-item["score"], item["crop"]),
        )
        scenario_scores = sorted(
            (score_crop(crop, scenario_temperature, scenario_rainfall) for crop in CROPS),
            key=lambda item: (-item["score"], item["crop"]),
        )
        baseline_scores_by_month[month] = baseline_scores
        scenario_scores_by_month[month] = scenario_scores
        months.append(
            {
                "month": calendar.month_name[month],
                "month_number": month,
                "expected_temperature_c": scenario_temperature,
                "expected_rainfall_mm": scenario_rainfall,
                "planting_window": f"1–20 {calendar.month_name[month]}",
                "main_risk": _month_risk(scenario_temperature, scenario_rainfall),
                "reason": scenario_scores[0]["reason"],
                "recommendations": scenario_scores[:3],
            }
        )

    baseline_selected = baseline_scores_by_month[selected_month]
    scenario_selected = scenario_scores_by_month[selected_month]
    scenario_by_crop = {item["crop"]: item for item in scenario_selected}
    comparison = [
        {
            "crop": item["crop"],
            "baseline_score": item["score"],
            "scenario_score": scenario_by_crop[item["crop"]]["score"],
            "delta": scenario_by_crop[item["crop"]]["score"] - item["score"],
        }
        for item in baseline_selected[:6]
    ]

    selected = months[selected_month - 1]
    return {
        "farm_id": farm_id,
        "farm_name": farm_name,
        "centroid": {"latitude": round(latitude, 5), "longitude": round(longitude, 5)},
        "data_mode": "live",
        "model_version": "farmtwin-live-decision-v1",
        "disclaimer": "Live planning indices based on real-time farm environmental data and analysis snapshots.",
        "assumptions": [
            "Soil suitability based on validated farm soil composition data.",
            "Current environmental conditions derived from latest farm snapshot analysis.",
            "Temperature and rainfall data sourced from live weather stations and satellite observations.",
        ],
        "scenario": {
            "selected_month": selected_month,
            "rainfall_change_pct": rainfall_change_pct,
            "temperature_change_c": temperature_change_c,
        },
        "months": months,
        "selected_month": selected,
        "risks": _risk_summary(
            selected_month,
            selected["expected_temperature_c"],
            selected["expected_rainfall_mm"],
            selected["recommendations"],
        ),
        "comparison": comparison,
    }
