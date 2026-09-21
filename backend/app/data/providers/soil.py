"""
Soil data provider adapter — SoilGrids REST API (ISRIC).

Retrieves modelled soil properties for a farm centroid at two depth intervals:
  - 0–5 cm
  - 5–15 cm

Properties requested: bdod (bulk density), clay, sand, silt, phh2o (pH),
soc (organic carbon).

For each property at each depth, stores:
  - mean prediction
  - 5th percentile (lower uncertainty bound)
  - 95th percentile (upper uncertainty bound)

All returned values are labelled as "modelled estimate" (not measured).
The adapter flags when the farm area is smaller than one SoilGrids grid cell
(250 m nominal resolution → ~0.625 ha).

Returns a ProviderResult with a soil payload on success.
Sets evidence_status="unavailable" when SoilGrids is unreachable or returns
an error.

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6
"""

from __future__ import annotations

import asyncio
import logging
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

SOILGRIDS_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"
SOURCE_NAME = "soilgrids-v2"
REQUEST_TIMEOUT_SECONDS = 20.0
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 0.25
RETRYABLE_STATUS_CODES = frozenset({502, 503, 504})

# Nominal spatial resolution of SoilGrids (metres)
RESOLUTION_M = 250

# SoilGrids grid cell area: 250 m × 250 m = 62500 m² = 0.625 ha
# Flag farms smaller than one grid cell.
SOILGRIDS_CELL_SIZE_M = 250
SOILGRIDS_CELL_AREA_HA = (SOILGRIDS_CELL_SIZE_M ** 2) / 10_000.0  # 0.625 ha

# Depth intervals to request
DEPTHS = ["0-5cm", "5-15cm"]

# Soil properties to request and their metadata
_PROPERTIES: dict[str, dict[str, str]] = {
    "bdod": {"label": "bulk density", "unit": "kg/dm³", "scale_factor_key": "bdod"},
    "clay": {"label": "clay fraction", "unit": "percent"},
    "sand": {"label": "sand fraction", "unit": "percent"},
    "silt": {"label": "silt fraction", "unit": "percent"},
    "phh2o": {"label": "pH (water)", "unit": "dimensionless"},
    "soc": {"label": "soil organic carbon", "unit": "g/kg"},
}

# SoilGrids returns values scaled by a factor; we need to divide to get real units
# Reference: https://www.isric.org/explore/soilgrids/faq-soilgrids#what_do_the_different_layers_mean
_SCALE_FACTORS: dict[str, float] = {
    "bdod": 100.0,    # cg/cm³ → kg/dm³: divide by 100
    "clay": 10.0,     # g/kg → percent: divide by 10
    "sand": 10.0,
    "silt": 10.0,
    "phh2o": 10.0,    # pH ×10 → pH: divide by 10
    "soc": 10.0,      # dg/kg → g/kg: divide by 10
}

# Statistics requested from SoilGrids
_STAT_KEYS = ["mean", "Q0.05", "Q0.95"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _scale_value(prop: str, raw_value: float | None) -> float | None:
    """Apply SoilGrids unit conversion scale factor to a raw returned value.

    SoilGrids returns integer-encoded values; we divide by the scale factor
    to get standard units.
    """
    if raw_value is None:
        return None
    factor = _SCALE_FACTORS.get(prop, 1.0)
    if factor == 0.0:
        return None
    return round(raw_value / factor, 6)


def _extract_property_values(
    layers: list[dict],
    prop_name: str,
    depth_label: str,
) -> dict[str, float | None]:
    """Extract mean, Q0.05, Q0.95 for a given property and depth from SoilGrids response.

    SoilGrids response structure:
      layers[n].name == prop_name
      layers[n].depths[m].label == depth_label
      layers[n].depths[m].values == {"mean": ..., "Q0.05": ..., "Q0.95": ...}

    Returns a dict with keys: mean, uncertainty_5th, uncertainty_95th.
    All values may be None if not found in the response.
    """
    result: dict[str, float | None] = {
        "mean": None,
        "uncertainty_5th": None,
        "uncertainty_95th": None,
    }

    for layer in layers:
        if layer.get("name") != prop_name:
            continue
        for depth in layer.get("depths", []):
            if depth.get("label") != depth_label:
                continue
            values = depth.get("values", {})
            result["mean"] = _scale_value(prop_name, values.get("mean"))
            result["uncertainty_5th"] = _scale_value(prop_name, values.get("Q0.05"))
            result["uncertainty_95th"] = _scale_value(prop_name, values.get("Q0.95"))
            return result

    return result


def _build_property_depth_payload(
    prop: str,
    depth: str,
    values: dict[str, float | None],
    retrieved_at: str,
    data_mode: str,
) -> dict:
    """Build the payload for a single property at a single depth.

    Each value is wrapped in a provenance envelope with modelled_estimate: true.

    Requirements: 5.3, 5.4
    """
    meta = _PROPERTIES[prop]
    unit = meta["unit"]

    mean_val = values["mean"]
    q5_val = values["uncertainty_5th"]
    q95_val = values["uncertainty_95th"]

    quality = EVIDENCE_ACCEPTED if mean_val is not None else EVIDENCE_UNAVAILABLE

    base_envelope = provenance_envelope(
        value=mean_val,
        unit=unit,
        source=SOURCE_NAME,
        acquired_at=retrieved_at,  # modelled data has no specific acquisition date
        retrieved_at=retrieved_at,
        data_mode=data_mode,
        quality=quality,
        resolution_m=float(RESOLUTION_M),
    )
    # Append modelled estimate flag per Requirements 5.4
    base_envelope["modelled_estimate"] = True

    return {
        "mean": base_envelope,
        "uncertainty_5th_percentile": (
            {**provenance_envelope(
                value=q5_val,
                unit=unit,
                source=SOURCE_NAME,
                acquired_at=retrieved_at,
                retrieved_at=retrieved_at,
                data_mode=data_mode,
                quality=quality if q5_val is not None else EVIDENCE_UNAVAILABLE,
                resolution_m=float(RESOLUTION_M),
            ), "modelled_estimate": True}
            if q5_val is not None
            else None
        ),
        "uncertainty_95th_percentile": (
            {**provenance_envelope(
                value=q95_val,
                unit=unit,
                source=SOURCE_NAME,
                acquired_at=retrieved_at,
                retrieved_at=retrieved_at,
                data_mode=data_mode,
                quality=quality if q95_val is not None else EVIDENCE_UNAVAILABLE,
                resolution_m=float(RESOLUTION_M),
            ), "modelled_estimate": True}
            if q95_val is not None
            else None
        ),
        "property_label": meta["label"],
        "depth": depth,
    }


def _build_soil_payload(
    api_response: dict,
    retrieved_at: str,
    data_mode: str,
    farm_area_ha: float | None,
) -> dict:
    """Build the full soil payload from a SoilGrids API response.

    Requirements: 5.1, 5.2, 5.3, 5.4, 5.6
    """
    layers = api_response.get("properties", {}).get("layers", [])

    # Build per-depth, per-property payloads
    depths_payload: dict[str, Any] = {}
    for depth in DEPTHS:
        depth_key = depth.replace("-", "_").replace("cm", "cm")
        props_at_depth: dict[str, Any] = {}
        for prop in _PROPERTIES:
            values = _extract_property_values(layers, prop, depth)
            props_at_depth[prop] = _build_property_depth_payload(
                prop=prop,
                depth=depth,
                values=values,
                retrieved_at=retrieved_at,
                data_mode=data_mode,
            )
        depths_payload[depth_key] = props_at_depth

    # Spatial metadata
    resolution_note = f"SoilGrids nominal resolution: {RESOLUTION_M} m"
    smaller_than_cell = (
        farm_area_ha is not None and farm_area_ha < SOILGRIDS_CELL_AREA_HA
    )

    return {
        "depths": depths_payload,
        "source_resolution_m": RESOLUTION_M,
        "resolution_note": resolution_note,
        "smaller_than_grid_cell": smaller_than_cell,
        "grid_cell_area_ha": SOILGRIDS_CELL_AREA_HA,
        "farm_area_ha": farm_area_ha,
        "modelled_estimate": True,
        "retrieved_at": retrieved_at,
    }


def _has_usable_mean(payload: dict) -> bool:
    """Return true when at least one requested property has a real mean value."""
    return any(
        prop_data.get("mean", {}).get("value") is not None
        for depth_data in payload.get("depths", {}).values()
        for prop_data in depth_data.values()
    )


def _build_unavailable_payload(retrieved_at: str, data_mode: str) -> dict:
    """Build a payload signalling soil data is unavailable.

    All property values are null envelopes.

    Requirements: 5.5
    """
    depths_payload: dict[str, Any] = {}
    for depth in DEPTHS:
        depth_key = depth.replace("-", "_").replace("cm", "cm")
        props_at_depth: dict[str, Any] = {}
        for prop, meta in _PROPERTIES.items():
            props_at_depth[prop] = {
                "mean": {
                    **null_envelope(
                        unit=meta["unit"],
                        source=SOURCE_NAME,
                        retrieved_at=retrieved_at,
                        data_mode=data_mode,
                    ),
                    "modelled_estimate": True,
                },
                "uncertainty_5th_percentile": None,
                "uncertainty_95th_percentile": None,
                "property_label": meta["label"],
                "depth": depth,
            }
        depths_payload[depth_key] = props_at_depth

    return {
        "depths": depths_payload,
        "source_resolution_m": RESOLUTION_M,
        "resolution_note": f"SoilGrids nominal resolution: {RESOLUTION_M} m",
        "smaller_than_grid_cell": None,
        "grid_cell_area_ha": SOILGRIDS_CELL_AREA_HA,
        "farm_area_ha": None,
        "modelled_estimate": True,
        "retrieved_at": retrieved_at,
    }


# ---------------------------------------------------------------------------
# Public adapter interface
# ---------------------------------------------------------------------------

async def fetch(
    centroid_lat: float,
    centroid_lon: float,
    farm_area_ha: float | None = None,
    data_mode: str = "live",
) -> ProviderResult:
    """Fetch soil properties from SoilGrids for the given centroid.

    Args:
        centroid_lat: Farm centroid latitude in decimal degrees.
        centroid_lon: Farm centroid longitude in decimal degrees.
        farm_area_ha: Farm area in hectares (used to flag sub-cell farms).
            May be None if area is unknown.
        data_mode: Data mode label for provenance (default "live").

    Returns:
        ProviderResult with a soil payload and evidence_status="accepted"
        on success, or evidence_status="unavailable" on any failure.

    Never raises; all errors are captured and returned as ProviderResult.

    Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6
    """
    retrieved_at = _iso_now()

    params = {
        "lon": centroid_lon,
        "lat": centroid_lat,
        "property": list(_PROPERTIES.keys()),
        "depth": DEPTHS,
        "value": _STAT_KEYS,
    }

    data: dict | None = None
    last_error: str | None = None
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await client.get(SOILGRIDS_URL, params=params)
                response.raise_for_status()
                decoded = response.json()
                if not isinstance(decoded, dict):
                    raise ValueError("response root is not a JSON object")
                properties = decoded.get("properties")
                if not isinstance(properties, dict):
                    raise ValueError("response properties is not a JSON object")
                layers = properties.get("layers")
                if not isinstance(layers, list) or not layers:
                    raise ValueError("response has no SoilGrids property layers")
                data = decoded
                break
            except httpx.TimeoutException as exc:
                last_error = f"SoilGrids request timed out after {REQUEST_TIMEOUT_SECONDS}s: {exc}"
                retryable = True
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                last_error = f"SoilGrids returned HTTP {status_code}"
                retryable = status_code in RETRYABLE_STATUS_CODES
            except (TypeError, ValueError) as exc:
                last_error = f"SoilGrids returned a malformed response: {exc}"
                retryable = False
            except Exception as exc:
                last_error = f"SoilGrids request failed: {exc}"
                retryable = False

            if not retryable or attempt == MAX_ATTEMPTS:
                break
            await asyncio.sleep(RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)))

    if data is None:
        msg = last_error or "SoilGrids request failed without an error message"
        logger.warning(msg)
        return ProviderResult(
            payload=_build_unavailable_payload(retrieved_at, data_mode),
            evidence_status=EVIDENCE_UNAVAILABLE,
            error_message=msg,
        )

    try:
        payload = _build_soil_payload(data, retrieved_at, data_mode, farm_area_ha)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        msg = f"SoilGrids returned a malformed response: {exc}"
        logger.warning(msg)
        return ProviderResult(
            payload=_build_unavailable_payload(retrieved_at, data_mode),
            evidence_status=EVIDENCE_UNAVAILABLE,
            error_message=msg,
        )
    if not _has_usable_mean(payload):
        msg = "SoilGrids response contained no usable mean property values"
        logger.warning(msg)
        return ProviderResult(
            payload=payload,
            evidence_status=EVIDENCE_UNAVAILABLE,
            error_message=msg,
        )
    return ProviderResult(
        payload=payload,
        evidence_status=EVIDENCE_ACCEPTED,
        error_message=None,
    )
