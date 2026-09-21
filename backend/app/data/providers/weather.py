"""
Weather provider adapter — Open-Meteo /v1/forecast.

Retrieves current conditions and 7-day hourly forecast for a farm centroid.
Returns a ProviderResult with a provenance envelope per field.
Sets evidence_status="unavailable" on any network error or non-2xx response.

Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Any

import httpx

from .base import (
    EVIDENCE_ACCEPTED,
    EVIDENCE_UNAVAILABLE,
    ProviderResult,
    null_envelope,
    provenance_envelope,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
SOURCE_NAME = "open-meteo"
REQUEST_TIMEOUT_SECONDS = 10.0

# Nominal grid resolution of Open-Meteo (derived from ERA5/GFS, ~11 km)
RESOLUTION_M = 11_000

# Fields to request from the current_weather API and hourly
CURRENT_VARIABLES = [
    "temperature_2m",
    "precipitation",
    "relative_humidity_2m",
    "wind_speed_10m",
    "wind_direction_10m",
    "cloud_cover",
    "weather_code",
]

# Unit mapping: variable name → (unit string, description)
_UNITS: dict[str, str] = {
    "temperature_2m": "celsius",
    "precipitation": "mm",
    "relative_humidity_2m": "percent",
    "wind_speed_10m": "m/s",
    "wind_direction_10m": "degrees",
    "cloud_cover": "percent",
    "weather_code": "wmo code",
}

# Retry config for transient server errors (5xx, 429)
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_RETRY_BASE_SECONDS = 2.0
_RETRY_MAX_SECONDS = 30.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


def _extract_current_value(data: dict, variable: str) -> float | None:
    """Use the provider's explicit current observation, never a future hour."""
    value = (data.get("current") or {}).get(variable)
    return float(value) if isinstance(value, (int, float)) else None


def _build_weather_payload(data: dict, retrieved_at: str, data_mode: str) -> dict:
    current = data.get("current") or {}
    acquired_at = current.get("time")
    if acquired_at and not acquired_at.endswith("Z") and "+" not in acquired_at:
        acquired_at += "+00:00"
    fields = {}
    for var in CURRENT_VARIABLES:
        value = _extract_current_value(data, var)
        unit = (data.get("current_units") or {}).get(var, _UNITS[var])
        if var == "wind_speed_10m" and value is not None:
            if unit == "km/h":
                value /= 3.6
            elif unit in ("mph", "mp/h"):
                value *= 0.44704
            elif unit not in ("m/s", "ms"):
                value = None
            unit = "m/s"
        fields[var] = provenance_envelope(
            value=value, unit=unit, source=SOURCE_NAME,
            acquired_at=acquired_at, retrieved_at=retrieved_at,
            data_mode=data_mode,
            quality=EVIDENCE_ACCEPTED if value is not None and acquired_at else EVIDENCE_UNAVAILABLE,
            resolution_m=RESOLUTION_M,
        )
    return {
        "fields": fields,
        "hourly": data.get("hourly") or {},
        "hourly_units": data.get("hourly_units") or {},
        "daily": data.get("daily") or {},
        "daily_units": data.get("daily_units") or {},
        "model_metadata": {
            "source": SOURCE_NAME, "issue_time": None,
            "valid_time": acquired_at, "forecast_horizon_days": 7,
            "retrieved_at": retrieved_at,
        },
    }


def _build_unavailable_payload(retrieved_at: str, data_mode: str) -> dict:
    """Build a payload where all values are null (provider unavailable).

    Requirements: 2.4, 2.5
    """
    fields: dict[str, Any] = {}
    for var in CURRENT_VARIABLES:
        fields[var] = null_envelope(
            unit=_UNITS.get(var, "unknown"),
            source=SOURCE_NAME,
            retrieved_at=retrieved_at,
            data_mode=data_mode,
        )
    return {"fields": fields, "model_metadata": None}


# ---------------------------------------------------------------------------
# Public adapter interface
# ---------------------------------------------------------------------------

async def fetch(
    centroid_lat: float,
    centroid_lon: float,
    data_mode: str = "live",
) -> ProviderResult:
    """Fetch weather data from Open-Meteo for the given centroid.

    Args:
        centroid_lat: Farm centroid latitude in decimal degrees.
        centroid_lon: Farm centroid longitude in decimal degrees.
        data_mode: Data mode label for provenance (default "live").

    Returns:
        ProviderResult with a weather payload and evidence_status="accepted"
        on success, or evidence_status="unavailable" on any failure.

    Never raises; all errors are captured and returned as ProviderResult.

    Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6
    """
    retrieved_at = _iso_now()

    params = {
        "latitude": centroid_lat,
        "longitude": centroid_lon,
        "current": ",".join(CURRENT_VARIABLES),
        "hourly": ",".join(CURRENT_VARIABLES),
        "daily": "temperature_2m_min,temperature_2m_max,temperature_2m_mean,precipitation_sum,wind_speed_10m_max,weather_code",
        "wind_speed_unit": "ms",
        "forecast_days": 7,
        "timezone": "UTC",
    }

    last_error: str = "Unknown error"
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = await client.get(OPEN_METEO_FORECAST_URL, params=params)

                # Handle Retry-After header on 429
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after and retry_after.isdigit() else _RETRY_BASE_SECONDS * attempt
                    logger.warning(
                        "Open-Meteo rate-limited (429), attempt %d/%d, waiting %.1fs",
                        attempt, _MAX_RETRIES, wait,
                    )
                    if attempt < _MAX_RETRIES:
                        await asyncio.sleep(min(wait, _RETRY_MAX_SECONDS))
                        continue
                    last_error = f"Open-Meteo rate-limited (429) after {attempt} attempts"
                    break

                if response.status_code in _RETRYABLE_STATUS_CODES:
                    last_error = f"Open-Meteo returned HTTP {response.status_code}"
                    logger.warning(
                        "%s, attempt %d/%d", last_error, attempt, _MAX_RETRIES
                    )
                    if attempt < _MAX_RETRIES:
                        wait = min(
                            _RETRY_BASE_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 1),
                            _RETRY_MAX_SECONDS,
                        )
                        await asyncio.sleep(wait)
                        continue
                    break

                response.raise_for_status()
                data = response.json()
                payload = _build_weather_payload(data, retrieved_at, data_mode)
                return ProviderResult(
                    payload=payload,
                    evidence_status=EVIDENCE_ACCEPTED,
                    error_message=None,
                )

            except httpx.TimeoutException as exc:
                last_error = f"Open-Meteo request timed out after {REQUEST_TIMEOUT_SECONDS}s: {exc}"
                logger.warning("%s, attempt %d/%d", last_error, attempt, _MAX_RETRIES)
                if attempt < _MAX_RETRIES:
                    wait = min(_RETRY_BASE_SECONDS * attempt, _RETRY_MAX_SECONDS)
                    await asyncio.sleep(wait)
                    continue
                break
            except httpx.HTTPStatusError as exc:
                last_error = f"Open-Meteo returned HTTP {exc.response.status_code}"
                logger.warning(last_error)
                break
            except Exception as exc:
                last_error = f"Open-Meteo request failed: {exc}"
                logger.warning(last_error)
                break

    return ProviderResult(
        payload=_build_unavailable_payload(retrieved_at, data_mode),
        evidence_status=EVIDENCE_UNAVAILABLE,
        error_message=last_error,
    )
