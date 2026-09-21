"""
Conduit eligibility adapter — assess whether a Conduit station's observations
are geographically eligible to contribute to a farm snapshot.

Eligibility criteria (Requirements 9.1):
  - Geodesic distance from station to farm centroid ≤ 50 km
  - Elevation difference ≤ 500 m (only checked when terrain data available)

When eligible, the adapter fetches the latest accepted HourlyAggregate
within 24 hours (Requirements 9.2, 9.4).

Returns a ProviderResult:
  - evidence_status = "accepted"  → station eligible, aggregate found
  - evidence_status = "ineligible" → station fails distance/elevation check
  - evidence_status = "unavailable" → eligible but no recent aggregate
  - evidence_status = "error"     → DB query failure

Requirements: 9.1, 9.2, 9.3, 9.4, 9.5
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from pyproj import Geod
from shapely.geometry import Point
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conduit import HourlyAggregate, QualityFlag, Station

from .base import (
    EVIDENCE_ACCEPTED,
    EVIDENCE_ERROR,
    EVIDENCE_INELIGIBLE,
    EVIDENCE_UNAVAILABLE,
    ProviderResult,
    provenance_envelope,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

DISTANCE_THRESHOLD_KM = 50.0
ELEVATION_DIFF_THRESHOLD_M = 500.0
AGGREGATE_WINDOW_HOURS = 24

SOURCE_NAME = "conduit"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _geodesic_distance_km(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Compute geodesic distance in kilometres between two WGS84 points.

    Uses the WGS84 ellipsoid via PyProj Geod.

    Requirements: 9.1
    """
    geod = Geod(ellps="WGS84")
    _az12, _az21, distance_m = geod.inv(lon1, lat1, lon2, lat2)
    return abs(distance_m) / 1000.0


def _build_conduit_payload(
    station: Station,
    aggregate: HourlyAggregate,
    distance_km: float,
    elevation_diff_m: float | None,
    eligibility_reason: str,
    retrieved_at: str,
    data_mode: str,
) -> dict[str, Any]:
    """Build the Conduit payload dict for an eligible station aggregate.

    Wraps each aggregate field in a provenance envelope.

    Requirements: 9.2, 9.5
    """
    # Format the aggregate window start as the acquisition time
    acquired_at = aggregate.window_start_utc.isoformat()

    def _field(value: float | None, unit: str) -> dict:
        return provenance_envelope(
            value=value,
            unit=unit,
            source=SOURCE_NAME,
            acquired_at=acquired_at,
            retrieved_at=retrieved_at,
            data_mode=data_mode,
            quality=EVIDENCE_ACCEPTED if value is not None else EVIDENCE_UNAVAILABLE,
            resolution_m=None,
        )

    return {
        "station_id": str(station.id),
        "station_name": station.name,
        "station_lat": station.latitude,
        "station_lon": station.longitude,
        "station_elevation_m": station.elevation_m,
        "distance_km": round(distance_km, 3),
        "elevation_diff_m": (
            round(elevation_diff_m, 1) if elevation_diff_m is not None else None
        ),
        "eligibility_reason": eligibility_reason,
        "window_start_utc": acquired_at,
        "data_mode": data_mode,
        # Temperature fields
        "temp_mean_celsius": _field(aggregate.temp_mean_celsius, "celsius"),
        "temp_min_celsius": _field(aggregate.temp_min_celsius, "celsius"),
        "temp_max_celsius": _field(aggregate.temp_max_celsius, "celsius"),
        # Humidity fields
        "humidity_mean_pct": _field(aggregate.humidity_mean_pct, "percent"),
        "humidity_min_pct": _field(aggregate.humidity_min_pct, "percent"),
        "humidity_max_pct": _field(aggregate.humidity_max_pct, "percent"),
        # Wind fields
        "wind_spd_mean_ms": _field(aggregate.wind_spd_mean_ms, "m/s"),
        "wind_spd_min_ms": _field(aggregate.wind_spd_min_ms, "m/s"),
        "wind_spd_max_ms": _field(aggregate.wind_spd_max_ms, "m/s"),
        # VPD
        "vpd_mean_kpa": _field(aggregate.vpd_mean_kpa, "kPa"),
        # Coverage metadata
        "observation_count": aggregate.observation_count,
        "accepted_count": aggregate.accepted_count,
        "coverage_ratio": aggregate.coverage_ratio,
    }


def _build_ineligible_payload(
    station: Station,
    distance_km: float,
    elevation_diff_m: float | None,
    reason: str,
) -> dict[str, Any]:
    """Build a payload for an ineligible station (no observations included).

    Requirements: 9.3
    """
    return {
        "station_id": str(station.id),
        "station_name": station.name,
        "station_lat": station.latitude,
        "station_lon": station.longitude,
        "station_elevation_m": station.elevation_m,
        "distance_km": round(distance_km, 3),
        "elevation_diff_m": (
            round(elevation_diff_m, 1) if elevation_diff_m is not None else None
        ),
        "eligibility_reason": reason,
        "ineligible": True,
    }


# ---------------------------------------------------------------------------
# Public adapter interface
# ---------------------------------------------------------------------------

async def fetch(
    farm_centroid: Point,
    session: AsyncSession,
    terrain_payload: dict | None = None,
    data_mode: str = "live",
) -> ProviderResult:
    """Assess Conduit station eligibility and fetch observations if eligible.

    Steps:
    1. Load all stations from the DB (typically one in demo setup).
    2. For each station, compute geodesic distance to the farm centroid.
    3. Check elevation difference if terrain data is available.
    4. If eligible, fetch the latest accepted HourlyAggregate within 24 h.
    5. Return ProviderResult with payload and evidence_status.

    When no station exists in the DB, returns evidence_status="unavailable"
    with a descriptive message.

    Args:
        farm_centroid: Shapely Point (lon, lat) in WGS84 for the farm centroid.
        session: Async SQLAlchemy session (read-only access).
        terrain_payload: Optional terrain payload dict; used to extract
            farm centroid elevation for elevation-difference check.
        data_mode: Provenance data mode label.

    Returns:
        ProviderResult. evidence_status values:
          "accepted"   — eligible station with a recent aggregate
          "ineligible" — station too distant or elevation diff too large
          "unavailable" — eligible but no aggregate within window, or no station
          "error"       — unexpected DB failure

    Requirements: 9.1, 9.2, 9.3, 9.4, 9.5
    """
    retrieved_at = _iso_now()

    # Extract farm centroid elevation from terrain payload if available
    farm_elevation_m: float | None = None
    if terrain_payload is not None:
        mean_elev = terrain_payload.get("mean_elevation_m")
        if isinstance(mean_elev, dict):
            farm_elevation_m = mean_elev.get("value")

    farm_lat = farm_centroid.y
    farm_lon = farm_centroid.x

    try:
        # Load all stations (small table in demo; typically one station)
        result = await session.execute(select(Station))
        stations: list[Station] = list(result.scalars().all())
    except Exception as exc:
        msg = f"Failed to query stations: {exc}"
        logger.error(msg)
        return ProviderResult(
            payload=None,
            evidence_status=EVIDENCE_ERROR,
            error_message=msg,
        )

    if not stations:
        return ProviderResult(
            payload=None,
            evidence_status=EVIDENCE_UNAVAILABLE,
            error_message="No Conduit stations found in the database.",
        )

    # Evaluate each station; use the first eligible one
    # (Phase 5 demo has a single station; multi-station selection deferred)
    for station in stations:
        if station.latitude is None or station.longitude is None:
            continue

        distance_km = _geodesic_distance_km(
            farm_lat, farm_lon, station.latitude, station.longitude
        )

        # Elevation difference check (when both elevations are available)
        elevation_diff_m: float | None = None
        if farm_elevation_m is not None and station.elevation_m is not None:
            elevation_diff_m = abs(farm_elevation_m - station.elevation_m)

        # --- Distance eligibility ---
        if distance_km > DISTANCE_THRESHOLD_KM:
            reason = (
                f"Station '{station.name}' is {distance_km:.1f} km away "
                f"(threshold: {DISTANCE_THRESHOLD_KM} km)."
            )
            logger.debug("Conduit station ineligible: %s", reason)
            return ProviderResult(
                payload=_build_ineligible_payload(
                    station, distance_km, elevation_diff_m, reason
                ),
                evidence_status=EVIDENCE_INELIGIBLE,
                error_message=reason,
            )

        # --- Elevation eligibility (only when both elevations known) ---
        if elevation_diff_m is not None and elevation_diff_m > ELEVATION_DIFF_THRESHOLD_M:
            reason = (
                f"Station '{station.name}' has an elevation difference of "
                f"{elevation_diff_m:.0f} m (threshold: {ELEVATION_DIFF_THRESHOLD_M} m)."
            )
            logger.debug("Conduit station ineligible (elevation): %s", reason)
            return ProviderResult(
                payload=_build_ineligible_payload(
                    station, distance_km, elevation_diff_m, reason
                ),
                evidence_status=EVIDENCE_INELIGIBLE,
                error_message=reason,
            )

        # --- Station is eligible — fetch latest accepted aggregate ---
        eligibility_reason = (
            f"Station '{station.name}' is {distance_km:.1f} km away"
            + (
                f", elevation difference {elevation_diff_m:.0f} m"
                if elevation_diff_m is not None
                else ""
            )
            + " — within thresholds."
        )

        cutoff_utc = datetime.now(tz=timezone.utc) - timedelta(hours=AGGREGATE_WINDOW_HOURS)

        try:
            agg_result = await session.execute(
                select(HourlyAggregate)
                .where(
                    HourlyAggregate.station_id == station.id,
                    HourlyAggregate.window_start_utc >= cutoff_utc,
                )
                .order_by(HourlyAggregate.window_start_utc.desc())
                .limit(1)
            )
            aggregate: HourlyAggregate | None = agg_result.scalar_one_or_none()
        except Exception as exc:
            msg = f"Failed to query hourly aggregates for station {station.id}: {exc}"
            logger.error(msg)
            return ProviderResult(
                payload=None,
                evidence_status=EVIDENCE_ERROR,
                error_message=msg,
            )

        if aggregate is None:
            msg = (
                f"Station '{station.name}' is eligible but has no accepted "
                f"hourly aggregate within the last {AGGREGATE_WINDOW_HOURS} hours."
            )
            logger.info(msg)
            return ProviderResult(
                payload={
                    "station_id": str(station.id),
                    "station_name": station.name,
                    "distance_km": round(distance_km, 3),
                    "elevation_diff_m": (
                        round(elevation_diff_m, 1)
                        if elevation_diff_m is not None
                        else None
                    ),
                    "eligibility_reason": eligibility_reason,
                    "no_recent_aggregate": True,
                },
                evidence_status=EVIDENCE_UNAVAILABLE,
                error_message=msg,
            )

        # Only use aggregates from accepted observations (Requirements 9.4)
        # HourlyAggregate.accepted_count tracks how many accepted observations
        # contributed. We allow the aggregate as long as accepted_count > 0.
        if aggregate.accepted_count == 0:
            msg = (
                f"Station '{station.name}' latest aggregate has "
                f"accepted_count=0 — no accepted observations."
            )
            logger.info(msg)
            return ProviderResult(
                payload={
                    "station_id": str(station.id),
                    "station_name": station.name,
                    "distance_km": round(distance_km, 3),
                    "eligibility_reason": eligibility_reason,
                    "no_accepted_observations": True,
                },
                evidence_status=EVIDENCE_UNAVAILABLE,
                error_message=msg,
            )

        payload = _build_conduit_payload(
            station=station,
            aggregate=aggregate,
            distance_km=distance_km,
            elevation_diff_m=elevation_diff_m,
            eligibility_reason=eligibility_reason,
            retrieved_at=retrieved_at,
            data_mode=data_mode,
        )
        return ProviderResult(
            payload=payload,
            evidence_status=EVIDENCE_ACCEPTED,
            error_message=None,
        )

    # No station with valid coordinates found
    return ProviderResult(
        payload=None,
        evidence_status=EVIDENCE_UNAVAILABLE,
        error_message="No Conduit stations with valid coordinates found.",
    )
