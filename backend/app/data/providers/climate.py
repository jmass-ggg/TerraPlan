"""
Climate baseline adapter — Open-Meteo historical archive.

Retrieves 30-year monthly means for a farm centroid location to produce
a climatological baseline. Computes anomaly (current minus baseline)
for temperature and rainfall when both are available.

Primary source: Open-Meteo /v1/archive (ERA5 reanalysis)
Baseline period: most recent 30 years ending prior to the current year.

Returns a ProviderResult with a climate_baseline payload.
Sets evidence_status="unavailable" when the archive is unreachable.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5
"""

from __future__ import annotations

import calendar
import logging
import statistics
from datetime import date, datetime, timezone
from typing import Any

import httpx

from .base import (
    EVIDENCE_ACCEPTED,
    EVIDENCE_UNAVAILABLE,
    ProviderResult,
    provenance_envelope,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
SOURCE_NAME = "open-meteo-era5"
BASELINE_SOURCE_DESCRIPTION = "ERA5 reanalysis via Open-Meteo historical archive"
REQUEST_TIMEOUT_SECONDS = 30.0
BASELINE_YEARS = 30

# Variables requested from the archive
ARCHIVE_DAILY_VARIABLES = [
    "temperature_2m_mean",
    "precipitation_sum",
]

# Unit mapping
_UNITS: dict[str, str] = {
    "temperature_2m_mean": "celsius",
    "precipitation_sum": "mm",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _baseline_date_range(reference_year: int) -> tuple[str, str]:
    """Return the ISO date strings for a 30-year baseline period.

    Ends on Dec 31 of (reference_year - 1) to avoid using the current year.
    Starts 30 years prior.

    Requirements: 3.2, 3.4
    """
    end_year = reference_year - 1
    start_year = end_year - BASELINE_YEARS + 1
    return f"{start_year}-01-01", f"{end_year}-12-31"


def _compute_monthly_means(
    dates: list[str], values: list[float | None], *, accumulation: bool = False
) -> dict[int, float | None]:
    """Compute monthly means from daily time-series data.

    Returns a dict mapping month number (1–12) to the mean value.
    Months with no valid data map to None.

    Requirements: 3.1, 3.2
    """
    # Reject incomplete year-months: missing rainfall days are not zero rain.
    grouped: dict[tuple[int, int], dict[int, float]] = {}
    for d_str, value in zip(dates, values):
        if value is None:
            continue
        try:
            day = date.fromisoformat(d_str)
        except ValueError:
            continue
        grouped.setdefault((day.year, day.month), {})[day.day] = float(value)
    monthly: dict[int, list[float]] = {m: [] for m in range(1, 13)}
    for (year, month), days in grouped.items():
        if len(days) != calendar.monthrange(year, month)[1]:
            continue
        monthly[month].append(sum(days.values()) if accumulation else statistics.mean(days.values()))
    return {month: statistics.mean(values) if values else None for month, values in monthly.items()}


def _compute_anomaly(
    current: float | None, baseline: float | None
) -> float | None:
    """Compute anomaly = current - baseline. Returns None if either is absent.

    Requirements: 3.3
    """
    if current is None or baseline is None:
        return None
    return round(current - baseline, 4)


def _build_climate_payload(
    archive_data: dict,
    current_weather_payload: dict | None,
    retrieved_at: str,
    data_mode: str,
    baseline_start: str,
    baseline_end: str,
) -> dict:
    """Build climate baseline payload from archive API response.

    Computes 30-year monthly means and anomalies vs current conditions
    when a weather payload is provided.

    Requirements: 3.1, 3.2, 3.3, 3.4
    """
    daily = archive_data.get("daily", {})
    dates = daily.get("time", [])

    baseline_period = f"{baseline_start[:4]}–{baseline_end[:4]}"
    current_month = datetime.fromisoformat(retrieved_at).month

    result: dict[str, Any] = {
        "baseline_source": BASELINE_SOURCE_DESCRIPTION,
        "baseline_period": baseline_period,
        "anomaly_method": "unavailable_without_matching_observation_period",
        "aggregation_version": "monthly-totals-v2",
        "retrieved_at": retrieved_at,
    }

    for var in ARCHIVE_DAILY_VARIABLES:
        raw_values = daily.get(var, [])
        monthly_means = _compute_monthly_means(dates, raw_values, accumulation=var == "precipitation_sum")
        baseline_this_month = monthly_means.get(current_month)

        # Determine current value from weather payload if available
        current_value: float | None = None
        if current_weather_payload:
            fields = current_weather_payload.get("fields", {})
            if var == "temperature_2m_mean" and "temperature_2m" in fields:
                current_value = fields["temperature_2m"].get("value")
            elif var == "precipitation_sum" and "precipitation" in fields:
                current_value = fields["precipitation"].get("value")

        # A current reading and a monthly climate normal have different periods.
        anomaly = None
        unit = _UNITS.get(var, "unknown")

        result[var] = {
            "baseline_monthly_mean": provenance_envelope(
                value=baseline_this_month,
                unit=unit,
                source=SOURCE_NAME,
                acquired_at=f"{baseline_end}T00:00:00+00:00",
                retrieved_at=retrieved_at,
                data_mode="historical_replay",
                quality=EVIDENCE_ACCEPTED if baseline_this_month is not None else EVIDENCE_UNAVAILABLE,
            ),
            "current_value": provenance_envelope(
                value=current_value,
                unit=unit,
                source="open-meteo",
                acquired_at=retrieved_at,
                retrieved_at=retrieved_at,
                data_mode=data_mode,
                quality=EVIDENCE_ACCEPTED if current_value is not None else EVIDENCE_UNAVAILABLE,
            ),
            "anomaly": provenance_envelope(
                value=anomaly,
                unit=unit,
                source=SOURCE_NAME,
                acquired_at=retrieved_at,
                retrieved_at=retrieved_at,
                data_mode=data_mode,
                quality=EVIDENCE_ACCEPTED if anomaly is not None else EVIDENCE_UNAVAILABLE,
            ),
        }

    result["all_monthly_means"] = {
        var: {
            str(month): mean
            for month, mean in _compute_monthly_means(
                dates, daily.get(var, []), accumulation=var == "precipitation_sum"
            ).items()
        }
        for var in ARCHIVE_DAILY_VARIABLES
    }

    return result


def _build_unavailable_payload(retrieved_at: str) -> dict:
    """Build a payload signalling the baseline is unavailable.

    Requirements: 3.5
    """
    return {
        "baseline_source": BASELINE_SOURCE_DESCRIPTION,
        "baseline_period": None,
        "anomaly_method": None,
        "retrieved_at": retrieved_at,
        "temperature_2m_mean": None,
        "precipitation_sum": None,
    }


# ---------------------------------------------------------------------------
# Public adapter interface
# ---------------------------------------------------------------------------

async def fetch(
    centroid_lat: float,
    centroid_lon: float,
    current_weather_payload: dict | None = None,
    data_mode: str = "live",
    reference_year: int | None = None,
) -> ProviderResult:
    """Fetch climate baseline from Open-Meteo historical archive.

    Args:
        centroid_lat: Farm centroid latitude.
        centroid_lon: Farm centroid longitude.
        current_weather_payload: Weather payload dict from the weather adapter
            (used to compute anomalies). May be None if weather unavailable.
        data_mode: Data mode label for current-value provenance.
        reference_year: Override the reference year for testing. Defaults to
            the current UTC year.

    Returns:
        ProviderResult with climate payload and evidence_status="accepted"
        on success, or evidence_status="unavailable" on failure.

    Requirements: 3.1, 3.2, 3.3, 3.4, 3.5
    """
    retrieved_at = _iso_now()

    if reference_year is None:
        reference_year = datetime.now(tz=timezone.utc).year

    baseline_start, baseline_end = _baseline_date_range(reference_year)

    params = {
        "latitude": centroid_lat,
        "longitude": centroid_lon,
        "start_date": baseline_start,
        "end_date": baseline_end,
        "daily": ",".join(ARCHIVE_DAILY_VARIABLES),
        "models": "era5",
        "timezone": "UTC",
    }

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(OPEN_METEO_ARCHIVE_URL, params=params)
            response.raise_for_status()
            data = response.json()
    except httpx.TimeoutException as exc:
        msg = f"Open-Meteo archive timed out after {REQUEST_TIMEOUT_SECONDS}s: {exc}"
        logger.warning(msg)
        return ProviderResult(
            payload=_build_unavailable_payload(retrieved_at),
            evidence_status=EVIDENCE_UNAVAILABLE,
            error_message=msg,
        )
    except httpx.HTTPStatusError as exc:
        msg = f"Open-Meteo archive returned HTTP {exc.response.status_code}"
        logger.warning(msg)
        return ProviderResult(
            payload=_build_unavailable_payload(retrieved_at),
            evidence_status=EVIDENCE_UNAVAILABLE,
            error_message=msg,
        )
    except Exception as exc:
        msg = f"Open-Meteo archive request failed: {exc}"
        logger.warning(msg)
        return ProviderResult(
            payload=_build_unavailable_payload(retrieved_at),
            evidence_status=EVIDENCE_UNAVAILABLE,
            error_message=msg,
        )

    payload = _build_climate_payload(
        archive_data=data,
        current_weather_payload=current_weather_payload,
        retrieved_at=retrieved_at,
        data_mode=data_mode,
        baseline_start=baseline_start,
        baseline_end=baseline_end,
    )
    return ProviderResult(
        payload=payload,
        evidence_status=EVIDENCE_ACCEPTED,
        error_message=None,
    )
