"""
Satellite imagery provider adapter — Copernicus Data Space Ecosystem STAC API.

Queries Sentinel-2-L2A scenes intersecting a farm polygon, downloads Band 4
(Red, 10 m) and Band 8 (NIR, 10 m), clips to the farm polygon, and computes:
  - NDVI = (B8 - B4) / (B8 + B4 + ε)   [ε = 1e-10, guards zero denominator]
  - NDMI = (B8A - B11) / (B8A + B11 + ε)   [spectral moisture proxy, 20 m]

Scene filters:
  - cloud_cover < 30 %
  - acquisition within 30 days of the job run
  - scene intersects the farm polygon

Returns a ProviderResult with a satellite payload on success, or
evidence_status="unavailable" when no qualifying scene exists or the STAC
API is unreachable.

NDMI NOTE: NDMI uses Sentinel-2 Band 8A (Vegetation Red Edge / NIR narrow,
842–865 nm, 20 m) and Band 11 (SWIR-1, 1565–1655 nm, 20 m). It is a
*spectral moisture proxy* that correlates with canopy water content.
It is NOT a direct measurement of soil volumetric water content.
Formula: NDMI = (B8A − B11) / (B8A + B11)

Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8
"""

from __future__ import annotations

import io
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import numpy as np
import rasterio
import rasterio.features
import rasterio.mask
import rasterio.transform
from rasterio.crs import CRS
from rasterio.warp import calculate_default_transform, reproject, Resampling
from shapely.geometry import Polygon, box, mapping, shape

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

STAC_SEARCH_URL = (
    "https://stac.dataspace.copernicus.eu/v1/search"
)
SOURCE_NAME = "sentinel-2-l2a"
COLLECTION = "sentinel-2-l2a"
CLOUD_COVER_THRESHOLD = 30.0          # percent
SCENE_MAX_AGE_DAYS = 30
REQUEST_TIMEOUT_SECONDS = 30.0

# CDSE token endpoint (Keycloak resource-owner password flow, public client)
CDSE_TOKEN_URL = (
    "https://identity.dataspace.copernicus.eu"
    "/auth/realms/CDSE/protocol/openid-connect/token"
)
CDSE_CLIENT_ID = "cdse-public"

# Copernicus Data Space STAC asset key candidates per logical band.
# The catalog exposes resolution-suffixed keys (e.g. "B04_10m") as the
# primary assets.  Semantic / legacy keys ("B04", "red", "B4") are kept
# as fallbacks for other STAC catalogs.
#
# Key lookup order matters: most-specific first.
_BAND_ASSET_KEYS = {
    "B04": ["B04_10m", "B04_20m", "B04", "red", "B4"],
    "B08": ["B08_10m", "B08", "nir", "B8"],
    "B8A": ["B8A_20m", "B8A_60m", "B8A", "nir08"],
    "B11": ["B11_20m", "B11_60m", "B11", "swir16"],
}

# Nominal spatial resolutions (m)
RESOLUTION_10M = 10
RESOLUTION_20M = 20

# NDVI guard epsilon
_NDVI_EPS = 1e-10


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _date_range_filter(max_age_days: int = SCENE_MAX_AGE_DAYS) -> str:
    """Return an RFC 3339 datetime interval string for the STAC filter.

    e.g. "2026-08-08T00:00:00Z/2026-09-07T23:59:59Z"
    """
    now = datetime.now(tz=timezone.utc)
    start = now - timedelta(days=max_age_days)
    return f"{start.strftime('%Y-%m-%dT%H:%M:%SZ')}/{now.strftime('%Y-%m-%dT%H:%M:%SZ')}"


def compute_ndvi(b8: np.ndarray, b4: np.ndarray) -> np.ndarray:
    """Compute NDVI with epsilon guard to avoid zero-division.

    NDVI = (B8 - B4) / (B8 + B4 + ε)
    Output is float32 in [-1, 1]; nodata pixels (both bands == 0) are NaN.

    Requirements: 4.2
    """
    b8 = b8.astype(np.float32)
    b4 = b4.astype(np.float32)
    denom = b8 + b4 + _NDVI_EPS
    ndvi = (b8 - b4) / denom
    # Mark pixels where both bands are 0 as nodata
    nodata_mask = (b8 == 0) & (b4 == 0)
    ndvi = np.where(nodata_mask, np.nan, ndvi)
    return ndvi.astype(np.float32)


def compute_ndmi(b8a: np.ndarray, b11: np.ndarray) -> np.ndarray:
    """Compute NDMI (spectral moisture proxy) with epsilon guard.

    NDMI = (B8A - B11) / (B8A + B11 + ε)
    Output is float32 in [-1, 1]; nodata pixels are NaN.

    This is a spectral moisture proxy correlated with canopy water content.
    It is NOT a direct measurement of soil volumetric water content.

    Requirements: 4.3
    """
    b8a = b8a.astype(np.float32)
    b11 = b11.astype(np.float32)
    denom = b8a + b11 + _NDVI_EPS
    ndmi = (b8a - b11) / denom
    nodata_mask = (b8a == 0) & (b11 == 0)
    ndmi = np.where(nodata_mask, np.nan, ndmi)
    return ndmi.astype(np.float32)


def _pixel_stats(
    index_array: np.ndarray,
) -> tuple[float | None, float | None, int, int]:
    """Return (mean, std_dev, valid_count, total_count) for an index array.

    NaN values are treated as nodata/invalid.
    """
    total = index_array.size
    valid_mask = ~np.isnan(index_array)
    valid_count = int(np.sum(valid_mask))
    if valid_count == 0:
        return None, None, 0, total
    valid_values = index_array[valid_mask]
    mean_val = float(np.mean(valid_values))
    std_val = float(np.std(valid_values))
    return mean_val, std_val, valid_count, total


def _find_asset_href(assets: dict, band_keys: list[str]) -> str | None:
    """Find the best download URL for a band given a list of possible asset key names.

    Copernicus Data Space STAC exposes assets with resolution-suffixed keys
    (e.g. "B04_10m") and S3 URIs as the primary href.  The publicly-accessible
    HTTPS download URL is nested under ``asset["alternate"]["https"]["href"]``.

    Priority order:
      1. asset["alternate"]["https"]["href"]   — public HTTPS (may need auth)
      2. asset["href"]  if it starts with https://  — direct HTTPS
      3. asset["alternate"]["s3"]["href"]      — S3 (requires signed URL / IAM)

    Logs the keys tried so failures are diagnosable.
    """
    for key in band_keys:
        if key not in assets:
            continue
        asset = assets[key]
        # Prefer the HTTPS alternate (Copernicus Data Space pattern)
        https_href = (
            asset.get("alternate", {}).get("https", {}).get("href")
        )
        if https_href:
            logger.debug("Resolved band key %r → alternate HTTPS href", key)
            return https_href
        # Fall back to primary href if it is already HTTPS
        primary = asset.get("href", "")
        if primary.startswith("https://"):
            logger.debug("Resolved band key %r → primary HTTPS href", key)
            return primary
    # Nothing found — log what was available to help diagnose
    available = list(assets.keys())
    logger.debug(
        "_find_asset_href: tried keys %s; available asset keys: %s",
        band_keys,
        available,
    )
    return None


async def _fetch_cdse_token(
    username: str,
    password: str,
    client: httpx.AsyncClient,
) -> tuple[str | None, str | None]:
    """Obtain a short-lived CDSE access token via resource-owner password flow.

    Returns (access_token, None) on success or (None, reason_string) on failure.
    The token is valid for ~600 seconds; callers use it for the duration of
    one job run and do not cache it across jobs.

    Common CDSE error descriptions and their meaning:
      "Account is not fully set up"   — account registered but Terms of Service
                                        not yet accepted at dataspace.copernicus.eu
      "Invalid user credentials"      — wrong username or password
      "Account disabled"              — account suspended by CDSE

    Requirements: 4.1 (download authentication)
    """
    if not username or not password:
        reason = (
            "CDSE credentials not configured. "
            "Set CDSE_USERNAME and CDSE_PASSWORD in .env."
        )
        logger.warning(
            "provider=cdse component=satellite stage=authentication "
            "error_type=missing_credentials message=%r",
            reason,
        )
        return None, reason

    try:
        resp = await client.post(
            CDSE_TOKEN_URL,
            data={
                "grant_type": "password",
                "client_id": CDSE_CLIENT_ID,
                "username": username,
                "password": password,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        token: str = resp.json()["access_token"]
        logger.debug("provider=cdse stage=authentication status=ok")
        return token, None

    except httpx.HTTPStatusError as exc:
        # Extract the human-readable CDSE error_description when available
        cdse_desc = ""
        try:
            body = exc.response.json()
            cdse_desc = body.get("error_description") or body.get("error") or ""
        except Exception:
            cdse_desc = exc.response.text[:120]

        http_status = exc.response.status_code
        reason = (
            f"CDSE authentication failed (HTTP {http_status}): {cdse_desc}. "
            f"Username: {username}. "
            "If the error is 'Account is not fully set up', accept the Terms of Service "
            "at https://dataspace.copernicus.eu and complete your profile."
        )
        logger.warning(
            "provider=cdse component=satellite stage=authentication "
            "error_type=http_%d cdse_error=%r message=%r",
            http_status, cdse_desc, reason,
        )
        return None, reason

    except Exception as exc:
        reason = f"CDSE authentication error: {type(exc).__name__}: {exc}"
        logger.warning(
            "provider=cdse component=satellite stage=authentication "
            "error_type=%s message=%r",
            type(exc).__name__, reason,
        )
        return None, reason


async def _download_band_bytes(
    href: str,
    client: httpx.AsyncClient,
    token: str | None = None,
) -> bytes | None:
    """Download a raster band from a CDSE OData URL.

    Attaches a Bearer token when one is available.
    Returns None on any failure (HTTP error, timeout, network).
    """
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = await client.get(
            href,
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers=headers,
        )
        response.raise_for_status()
        return response.content
    except Exception as exc:
        logger.warning("Failed to download band from %s: %s", href, exc)
        return None


def _clip_and_read_band(
    band_bytes: bytes,
    farm_polygon: Polygon,
) -> np.ndarray | None:
    """Open a raster from bytes, reproject farm polygon mask, clip, and return array.

    Returns a 2D float32 numpy array, or None if the band cannot be read.

    Requirements: 4.4
    """
    try:
        with rasterio.open(io.BytesIO(band_bytes)) as src:
            # Reproject farm polygon to the raster CRS for masking
            if src.crs and src.crs.to_epsg() != 4326:
                from pyproj import Transformer
                transformer = Transformer.from_crs("EPSG:4326", src.crs.to_epsg(), always_xy=True)
                coords = list(farm_polygon.exterior.coords)
                projected_coords = [transformer.transform(lon, lat) for lon, lat in coords]
                mask_polygon = Polygon(projected_coords)
            else:
                mask_polygon = farm_polygon

            clipped, _ = rasterio.mask.mask(
                src,
                [mapping(mask_polygon)],
                crop=True,
                nodata=0,
                filled=True,
            )
            return clipped[0].astype(np.float32)
    except Exception as exc:
        logger.warning("Failed to clip band: %s", exc)
        return None


async def _search_scenes(
    farm_polygon: Polygon,
    client: httpx.AsyncClient,
    max_age_days: int = SCENE_MAX_AGE_DAYS,
) -> list[dict]:
    """Query the STAC API for qualifying Sentinel-2 scenes.

    Returns a list of STAC feature dicts, sorted by cloud cover ascending.
    Empty list if none found or on API error.

    Requirements: 4.1
    """
    bbox = list(farm_polygon.bounds)  # [minx, miny, maxx, maxy]
    datetime_filter = _date_range_filter(max_age_days)

    payload: dict[str, Any] = {
        "collections": [COLLECTION],
        "bbox": bbox,
        "datetime": datetime_filter,
        "limit": 10,
        "query": {
            "eo:cloud_cover": {"lt": CLOUD_COVER_THRESHOLD},
        },
    }

    try:
        response = await client.post(
            STAC_SEARCH_URL,
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        data = response.json()
        features = data.get("features", [])
        # Sort by cloud cover ascending to pick the clearest scene
        features.sort(key=lambda f: f.get("properties", {}).get("eo:cloud_cover", 100))
        return features
    except httpx.HTTPStatusError as exc:
        logger.warning("STAC search returned HTTP %s: %s", exc.response.status_code, exc)
        raise RuntimeError(f"STAC search returned HTTP {exc.response.status_code}") from exc
    except Exception as exc:
        logger.warning("STAC search failed: %s", exc)
        raise RuntimeError(f"STAC search failed: {type(exc).__name__}: {exc}") from exc


def _last_scene_age_days(features: list[dict]) -> int | None:
    """Return the age in days of the most recent scene in a feature list.

    Used when no qualifying scene meets the cloud-cover filter but older
    scenes may exist in a broader search.

    Requirements: 4.6
    """
    now = datetime.now(tz=timezone.utc)
    best_age: int | None = None
    for feat in features:
        dt_str = feat.get("properties", {}).get("datetime")
        if not dt_str:
            continue
        try:
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            age_days = (now - dt).days
            if best_age is None or age_days < best_age:
                best_age = age_days
        except ValueError:
            continue
    return best_age


def _build_unavailable_payload(
    retrieved_at: str,
    data_mode: str,
    last_scene_age_days: int | None = None,
    reason: str | None = None,
) -> dict:
    """Build a payload signalling satellite data is unavailable.

    Requirements: 4.6, 4.7
    """
    return {
        "ndvi": null_envelope(
            unit="dimensionless",
            source=SOURCE_NAME,
            retrieved_at=retrieved_at,
            data_mode=data_mode,
        ),
        "ndmi": null_envelope(
            unit="dimensionless",
            source=SOURCE_NAME,
            retrieved_at=retrieved_at,
            data_mode=data_mode,
        ),
        "valid_pixel_pct": None,
        "valid_pixel_count": None,
        "total_pixel_count": None,
        "scene_id": None,
        "acquisition_date": None,
        "cloud_cover_pct": None,
        "last_scene_age_days": last_scene_age_days,
        "unavailability_reason": reason,
        "ndvi_std": None,
        "ndmi_std": None,
        "ndmi_note": (
            "NDMI = (B8A - B11) / (B8A + B11). "
            "Spectral moisture proxy. Not a direct soil-water measurement."
        ),
    }


def _build_success_payload(
    ndvi_mean: float | None,
    ndvi_std: float | None,
    ndmi_mean: float | None,
    ndmi_std: float | None,
    valid_pixel_count: int,
    total_pixel_count: int,
    scene_id: str,
    acquisition_date: str,
    cloud_cover_pct: float,
    retrieved_at: str,
    data_mode: str,
) -> dict:
    """Build the satellite payload on successful index computation.

    Requirements: 4.2, 4.3, 4.4, 4.5, 4.8
    """
    valid_pixel_pct = (
        round(100.0 * valid_pixel_count / total_pixel_count, 2)
        if total_pixel_count > 0
        else 0.0
    )
    quality = EVIDENCE_ACCEPTED if ndvi_mean is not None else EVIDENCE_UNAVAILABLE

    return {
        "ndvi": provenance_envelope(
            value=ndvi_mean,
            unit="dimensionless",
            source=SOURCE_NAME,
            acquired_at=acquisition_date,
            retrieved_at=retrieved_at,
            data_mode=data_mode,
            quality=quality,
            resolution_m=RESOLUTION_10M,
        ),
        "ndmi": provenance_envelope(
            value=ndmi_mean,
            unit="dimensionless",
            source=SOURCE_NAME,
            acquired_at=acquisition_date,
            retrieved_at=retrieved_at,
            data_mode=data_mode,
            quality=quality,
            resolution_m=RESOLUTION_20M,
        ),
        "ndvi_std": ndvi_std,
        "ndmi_std": ndmi_std,
        "valid_pixel_pct": valid_pixel_pct,
        "valid_pixel_count": valid_pixel_count,
        "total_pixel_count": total_pixel_count,
        "scene_id": scene_id,
        "acquisition_date": acquisition_date,
        "cloud_cover_pct": cloud_cover_pct,
        "last_scene_age_days": None,
        "unavailability_reason": None,
        "ndmi_note": (
            "NDMI = (B8A - B11) / (B8A + B11). "
            "Spectral moisture proxy. Not a direct soil-water measurement."
        ),
    }


# ---------------------------------------------------------------------------
# Public adapter interface
# ---------------------------------------------------------------------------

async def fetch(
    farm_polygon: Polygon,
    data_mode: str = "live",
    cdse_username: str = "",
    cdse_password: str = "",
) -> ProviderResult:
    """Fetch satellite data for the farm polygon.

    Queries the Copernicus STAC API for the most recent qualifying
    Sentinel-2-L2A scene. Downloads bands, clips to the farm polygon,
    and computes NDVI and NDMI.

    Args:
        farm_polygon: Shapely Polygon in WGS84 (EPSG:4326).
        data_mode: Provenance data mode label.
        cdse_username: Copernicus Data Space Ecosystem account email.
        cdse_password: CDSE account password.

    Returns:
        ProviderResult with satellite payload and evidence_status="accepted",
        or evidence_status="unavailable" when no qualifying scene exists,
        credentials are missing, or the data source is unreachable.

    Never raises; all errors are captured.

    Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8
    """
    retrieved_at = _iso_now()

    async with httpx.AsyncClient() as client:
        # -----------------------------------------------------------------
        # Step 0: Obtain CDSE access token for authenticated band downloads
        # -----------------------------------------------------------------
        token, token_error = await _fetch_cdse_token(cdse_username, cdse_password, client)
        if not token:
            msg = token_error or "CDSE access token could not be obtained."
            return ProviderResult(
                payload=_build_unavailable_payload(retrieved_at, data_mode, reason=msg),
                evidence_status=EVIDENCE_UNAVAILABLE,
                error_message=msg,
            )

        # -----------------------------------------------------------------
        # Step 1: Search for qualifying scenes (cloud < 30%, last 30 days)
        # -----------------------------------------------------------------
        try:
            features = await _search_scenes(farm_polygon, client)
        except RuntimeError as exc:
            return ProviderResult(
                payload=_build_unavailable_payload(retrieved_at, data_mode, reason=str(exc)),
                evidence_status=EVIDENCE_UNAVAILABLE, error_message=str(exc),
            )

        if not features:
            # Try a broader search (no cloud filter) to report last_scene_age_days
            try:
                all_features = await _search_scenes(farm_polygon, client, max_age_days=365)
            except RuntimeError as exc:
                return ProviderResult(
                    payload=_build_unavailable_payload(retrieved_at, data_mode, reason=str(exc)),
                    evidence_status=EVIDENCE_UNAVAILABLE, error_message=str(exc),
                )
            last_age = _last_scene_age_days(all_features)
            msg = (
                f"No Sentinel-2 scene with cloud cover < {CLOUD_COVER_THRESHOLD}% "
                f"found within {SCENE_MAX_AGE_DAYS} days."
            )
            logger.info(msg)
            return ProviderResult(
                payload=_build_unavailable_payload(
                    retrieved_at=retrieved_at,
                    data_mode=data_mode,
                    last_scene_age_days=last_age,
                    reason=msg,
                ),
                evidence_status=EVIDENCE_UNAVAILABLE,
                error_message=msg,
            )

        # Pick the scene with the lowest cloud cover
        scene = features[0]
        props = scene.get("properties", {})
        assets = scene.get("assets", {})

        scene_id = scene.get("id", "unknown")
        cloud_cover_pct = float(props.get("eo:cloud_cover", 0.0))

        # Acquisition date (not retrieval date) — Requirements 4.5
        raw_dt = props.get("datetime") or props.get("start_datetime", retrieved_at)
        acquisition_date = raw_dt

        # -----------------------------------------------------------------
        # Step 2: Download Band 4 and Band 8 for NDVI (10 m)
        # -----------------------------------------------------------------
        b4_href = _find_asset_href(assets, _BAND_ASSET_KEYS["B04"])
        b8_href = _find_asset_href(assets, _BAND_ASSET_KEYS["B08"])

        if not b4_href or not b8_href:
            available_keys = sorted(assets.keys())
            msg = (
                f"Scene {scene_id}: Band 4 or Band 8 asset href not found in STAC response. "
                f"Available asset keys: {available_keys}"
            )
            logger.warning(msg)
            return ProviderResult(
                payload=_build_unavailable_payload(
                    retrieved_at=retrieved_at,
                    data_mode=data_mode,
                    reason=msg,
                ),
                evidence_status=EVIDENCE_UNAVAILABLE,
                error_message=msg,
            )

        b4_bytes, b8_bytes = await _download_band_bytes(
            b4_href, client, token
        ), await _download_band_bytes(b8_href, client, token)

        if b4_bytes is None or b8_bytes is None:
            msg = f"Scene {scene_id}: Failed to download Band 4 or Band 8."
            logger.warning(msg)
            return ProviderResult(
                payload=_build_unavailable_payload(
                    retrieved_at=retrieved_at,
                    data_mode=data_mode,
                    reason=msg,
                ),
                evidence_status=EVIDENCE_UNAVAILABLE,
                error_message=msg,
            )

        # -----------------------------------------------------------------
        # Step 3: Clip to farm polygon and compute NDVI
        # -----------------------------------------------------------------
        b4_array = _clip_and_read_band(b4_bytes, farm_polygon)
        b8_array = _clip_and_read_band(b8_bytes, farm_polygon)

        if b4_array is None or b8_array is None:
            msg = f"Scene {scene_id}: Could not clip Band 4 or Band 8 to farm polygon."
            logger.warning(msg)
            return ProviderResult(
                payload=_build_unavailable_payload(
                    retrieved_at=retrieved_at,
                    data_mode=data_mode,
                    reason=msg,
                ),
                evidence_status=EVIDENCE_UNAVAILABLE,
                error_message=msg,
            )

        ndvi_array = compute_ndvi(b8_array, b4_array)
        ndvi_mean, ndvi_std, valid_count, total_count = _pixel_stats(ndvi_array)

        # -----------------------------------------------------------------
        # Step 4: Download Band 8A and Band 11 for NDMI (20 m)
        # -----------------------------------------------------------------
        ndmi_mean: float | None = None
        ndmi_std: float | None = None

        b8a_href = _find_asset_href(assets, _BAND_ASSET_KEYS["B8A"])
        b11_href = _find_asset_href(assets, _BAND_ASSET_KEYS["B11"])

        if b8a_href and b11_href:
            b8a_bytes = await _download_band_bytes(b8a_href, client, token)
            b11_bytes = await _download_band_bytes(b11_href, client, token)
            if b8a_bytes and b11_bytes:
                b8a_array = _clip_and_read_band(b8a_bytes, farm_polygon)
                b11_array = _clip_and_read_band(b11_bytes, farm_polygon)
                if b8a_array is not None and b11_array is not None:
                    ndmi_array = compute_ndmi(b8a_array, b11_array)
                    ndmi_mean, ndmi_std, _, _ = _pixel_stats(ndmi_array)

        # -----------------------------------------------------------------
        # Step 5: Build and return result
        # -----------------------------------------------------------------
        payload = _build_success_payload(
            ndvi_mean=ndvi_mean,
            ndvi_std=ndvi_std,
            ndmi_mean=ndmi_mean,
            ndmi_std=ndmi_std,
            valid_pixel_count=valid_count,
            total_pixel_count=total_count,
            scene_id=scene_id,
            acquisition_date=acquisition_date,
            cloud_cover_pct=cloud_cover_pct,
            retrieved_at=retrieved_at,
            data_mode=data_mode,
        )

        return ProviderResult(
            payload=payload,
            evidence_status=EVIDENCE_ACCEPTED,
            error_message=None,
        )
