"""
Snapshot context adapter.

Converts a Phase 5 AnalysisSnapshot (or a demonstration profile) into a
unified SnapshotContext used by the CropEngine for scoring.

Public API:
    context_from_snapshot(snapshot, planting_month, crop_duration)
        → SnapshotContext  (source="snapshot")

    context_from_demonstration(latitude, longitude, planting_month)
        → SnapshotContext  (source="demonstration")

Requirements: 2.1, 2.2, 3.4
"""

from __future__ import annotations

import calendar
import math
from datetime import date, datetime, timedelta, timezone
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.models.snapshot import AnalysisSnapshot

from app.services.decision_support import _seasonal_climate  # type: ignore[private-usage]


# ---------------------------------------------------------------------------
# SnapshotContext dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SnapshotContext:
    """Unified environmental inputs passed to the CropEngine for scoring.

    All numeric fields are optional — None means "not available from this
    source".  The *demonstration_input_fields* frozenset documents which
    fields were filled from the demonstration fallback rather than a real
    snapshot.

    Requirements: 2.1, 2.2, 3.4
    """

    source: Literal["snapshot", "demonstration"]

    # Snapshot reference (null when source="demonstration")
    snapshot_id: str | None
    data_mode: str   # "live" | "historical_replay" | "demonstration"

    # Temperature — mean over the crop duration (°C)
    temperature_mean_c: float | None
    temperature_source: str | None

    # Rainfall — total over the crop duration (mm)
    rainfall_total_mm: float | None
    rainfall_source: str | None

    # Soil (0–5 cm depth)
    soil_ph: float | None
    soil_clay_pct: float | None
    soil_sand_pct: float | None
    soil_source: str | None
    soil_is_modelled: bool

    # Environmental condition — NDVI mean from satellite
    ndvi_mean: float | None
    ndvi_source: str | None

    # Conduit VPD (kPa) from an eligible Conduit station
    vpd_kpa: float | None

    # Completeness bookkeeping
    real_input_fields: frozenset[str]
    demonstration_input_fields: frozenset[str]

    # ---------------------------------------------------------------------------
    # Risk engine fields (Phase 7) — optional, default None for back-compat
    # ---------------------------------------------------------------------------

    # 7-day cumulative rainfall (mm) for heavy rainfall / flood assessments
    rain_7d_mm: float | None = None

    # Maximum wind speed (m/s) for wind hazard assessment
    wind_max_ms: float | None = None

    # Terrain slope (%) derived from mean_slope_deg (degrees → percent)
    slope_pct: float | None = None

    # Seasonal climate baseline rainfall (mm) for drought ratio calculation
    climate_baseline_rainfall_mm: float | None = None
    relative_humidity_pct: float | None = None
    irrigation_total_mm: float = 0.0


# ---------------------------------------------------------------------------
# Helper: safely extract a numeric value from a provenance envelope
# ---------------------------------------------------------------------------

def _env_value(envelope: dict | None) -> float | None:
    """Return the numeric value from a provenance envelope dict, or None."""
    if envelope is None:
        return None
    return envelope.get("value")


def _env_source(envelope: dict | None) -> str | None:
    """Return the source string from a provenance envelope dict, or None."""
    if envelope is None:
        return None
    return envelope.get("source")


def _is_accepted(envelope: dict | None) -> bool:
    """Return True when the envelope quality is 'accepted'."""
    if envelope is None:
        return False
    return envelope.get("quality") == "accepted"


# ---------------------------------------------------------------------------
# context_from_snapshot
# ---------------------------------------------------------------------------

def growing_interval(start: date, months: int) -> tuple[date, date]:
    end_month = start.month - 1 + months
    year, month = start.year + end_month // 12, end_month % 12 + 1
    return start, date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def seasonal_values(climate: dict, start: date, months: int) -> tuple[float | None, float | None]:
    """Integrate climate normals over the actual calendar interval, including leap days."""
    start, end = growing_interval(start, months)
    monthly = climate.get("all_monthly_means") or {}
    temperatures = monthly.get("temperature_2m_mean") or {}
    rain = monthly.get("precipitation_sum") or {}
    temp_sum, rain_sum, count = 0.0, 0.0, 0
    temp_complete = True
    rain_complete = True  # Provider stores monthly totals; require every season month below.
    day = start
    while day < end:
        temp, precip = temperatures.get(str(day.month)), rain.get(str(day.month))
        if temp is None:
            temp_complete = False
        else:
            temp_sum += float(temp)
        if precip is None:
            rain_complete = False
        else:
            rain_sum += float(precip) / calendar.monthrange(day.year, day.month)[1]
        count += 1
        day += timedelta(days=1)
    return (temp_sum / count if temp_complete and count else None,
            rain_sum if rain_complete and count else None)


def context_from_snapshot(
    snapshot: "AnalysisSnapshot", planting_month: int, crop_duration: int,
    planting_date: date | None = None,
) -> SnapshotContext:
    """Missing evidence stays missing. Climate normals are not a current forecast."""
    start = planting_date or date(datetime.now(timezone.utc).year, planting_month, 1)
    climate = snapshot.climate_baseline or {}
    temperature, rainfall = seasonal_values(climate, start, crop_duration)
    def accepted(envelope):
        value = _env_value(envelope) if _is_accepted(envelope) else None
        return float(value) if isinstance(value, (float, int)) and math.isfinite(value) else None
    soil = snapshot.soil or {}
    depths = soil.get("depths") or {}
    top = depths.get("0_5cm") or depths.get("0-5cm") or {}
    soil_values = {key: accepted((top.get(key) or {}).get("mean")) for key in ("phh2o", "clay", "sand")}
    satellite = snapshot.satellite or {}
    conduit = snapshot.conduit or {}
    weather = snapshot.weather or {}
    vpd = accepted(conduit.get("vpd_mean_kpa"))
    if vpd is None and snapshot.data_mode in ("live", "historical_replay"):
        fields = weather.get("fields") or {}
        temp_env, rh_env = fields.get("temperature_2m"), fields.get("relative_humidity_2m")
        if all((env or {}).get("data_mode") in ("live", "historical_replay") for env in (temp_env, rh_env)):
            current_temp, humidity = accepted(temp_env), accepted(rh_env)
            if current_temp is not None and humidity is not None and 0 < humidity < 100 and -100 < current_temp < 100:
                saturation = 0.6108 * math.exp(17.27 * current_temp / (current_temp + 237.3))
                vpd = saturation * (1 - humidity / 100)
    daily = weather.get("daily") or {}
    # Require seven complete daily totals. A partial forecast is not seven-day rainfall.
    rain_days = daily.get("precipitation_sum") or []
    rain_7d = sum(rain_days[:7]) if len(rain_days) >= 7 and all(v is not None for v in rain_days[:7]) else None
    wind_days = daily.get("wind_speed_10m_max") or []
    wind = max(wind_days[:7]) if len(wind_days) >= 7 and all(v is not None for v in wind_days[:7]) else None
    wind_unit = (weather.get("daily_units") or {}).get("wind_speed_10m_max")
    if wind is not None:
        wind = wind / 3.6 if wind_unit == "km/h" else wind if wind_unit in ("m/s", "ms") else None
    slope = accepted((snapshot.terrain or {}).get("mean_slope_deg"))
    values = {
        "temperature_mean_c": temperature, "rainfall_total_mm": rainfall,
        "soil_ph": soil_values["phh2o"], "soil_clay_pct": soil_values["clay"],
        "soil_sand_pct": soil_values["sand"], "ndvi_mean": accepted(satellite.get("ndvi")),
        "vpd_kpa": vpd,
    }
    return SnapshotContext(
        source="snapshot", snapshot_id=str(snapshot.id), data_mode=snapshot.data_mode,
        **values, temperature_source="open-meteo-era5" if temperature is not None else None,
        rainfall_source="open-meteo-era5" if rainfall is not None else None,
        soil_source="soilgrids" if any(v is not None for v in soil_values.values()) else None,
        soil_is_modelled=True, ndvi_source="sentinel-2" if values["ndvi_mean"] is not None else None,
        real_input_fields=frozenset(k for k, v in values.items() if v is not None),
        demonstration_input_fields=frozenset(), rain_7d_mm=rain_7d, wind_max_ms=wind,
        slope_pct=100 * math.tan(math.radians(slope)) if slope is not None else None,
        climate_baseline_rainfall_mm=rainfall,
        relative_humidity_pct=accepted((weather.get("fields") or {}).get("relative_humidity_2m")),
    )


def context_for_risks(snapshot: "AnalysisSnapshot") -> SnapshotContext:
    """Use current weather for current hazards; compare matched seven-day rain periods."""
    valid = snapshot.valid_time_utc
    context = context_from_snapshot(snapshot, valid.month, 1, valid.date())
    temp_env = ((snapshot.weather or {}).get("fields") or {}).get("temperature_2m")
    temperature = _env_value(temp_env) if _is_accepted(temp_env) else None
    monthly = ((snapshot.climate_baseline or {}).get("all_monthly_means") or {}).get("precipitation_sum") or {}
    baseline = 0.0
    for offset in range(7):
        day = valid.date() + timedelta(days=offset)
        value = monthly.get(str(day.month))
        if value is None or (snapshot.climate_baseline or {}).get("aggregation_version") != "monthly-totals-v2":
            baseline = None
            break
        # precipitation_sum is a climatological monthly total in millimetres.
        # Convert each covered month to a daily normal, then sum the matched 7 days.
        baseline += value / calendar.monthrange(day.year, day.month)[1]
    return replace(context, temperature_mean_c=temperature, rainfall_total_mm=context.rain_7d_mm,
                   climate_baseline_rainfall_mm=baseline)


def with_irrigation(context: SnapshotContext, mode: str, monthly_mm: float | None, duration: int) -> SnapshotContext:
    return replace(context, irrigation_total_mm=(monthly_mm or 0.0) * duration if mode == "irrigated" else 0.0)


# ---------------------------------------------------------------------------
# context_from_demonstration
# ---------------------------------------------------------------------------

def context_from_demonstration(
    latitude: float,
    longitude: float,
    planting_month: int,
    crop_duration: int = 4,
) -> SnapshotContext:
    """Build a demonstration SnapshotContext using the existing decision_support profile.

    Calls _seasonal_climate() to get temperature and monthly rainfall for the
    planting month, then multiplies rainfall by crop_duration to get total mm.

    All fields are sourced from demonstration; snapshot_id is None.

    Requirements: 2.2, 3.4
    """
    seasons = [_seasonal_climate(latitude, longitude, (planting_month - 1 + i) % 12 + 1) for i in range(crop_duration)]
    temperature_c = sum(t for t, _ in seasons) / crop_duration
    rainfall_total_mm = sum(r for _, r in seasons)

    # Demonstration uses neutral soil and environmental placeholders
    # (documented in the decision_support.py comments)
    all_fields = frozenset({
        "temperature_mean_c",
        "rainfall_total_mm",
        "soil_ph",
        "soil_clay_pct",
        "soil_sand_pct",
        "ndvi_mean",
        "vpd_kpa",
    })

    return SnapshotContext(
        source="demonstration",
        snapshot_id=None,
        data_mode="demonstration",
        temperature_mean_c=temperature_c,
        temperature_source="decision_support_demonstration",
        rainfall_total_mm=rainfall_total_mm,
        rainfall_source="decision_support_demonstration",
        # Neutral soil placeholders — not real farm measurements
        soil_ph=None,
        soil_clay_pct=None,
        soil_sand_pct=None,
        soil_source=None,
        soil_is_modelled=False,
        # Neutral environmental placeholder — not a real farm measurement
        ndvi_mean=None,
        ndvi_source=None,
        vpd_kpa=None,
        real_input_fields=frozenset(),
        demonstration_input_fields=all_fields,
    )
