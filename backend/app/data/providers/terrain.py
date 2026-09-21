"""
Terrain data provider adapter — Copernicus DEM GLO-30.

Downloads Copernicus Digital Elevation Model tiles at 30 m resolution for
the farm polygon extent, clips to the farm polygon, and computes:
  - mean_elevation_m
  - min_elevation_m
  - max_elevation_m
  - mean_slope_deg  (Horn 1981 gradient estimator)

Metadata recorded:
  - DEM source: "copernicus-dem-glo30"
  - Resolution: 30 m
  - Vertical reference: EGM2008

flood_probability is explicitly set to null — slope and elevation alone are
insufficient to claim flood probability; drainage evidence is required.

Returns a ProviderResult with a terrain payload on success.
Sets evidence_status="unavailable" when the DEM source is unreachable or
tile download fails.

Requirements: 6.1, 6.2, 6.3, 6.4, 6.5
"""

from __future__ import annotations

import io
import logging
import math
from datetime import datetime, timezone
from typing import Any

import httpx
import numpy as np
import rasterio
import rasterio.mask
import rasterio.transform
from rasterio.crs import CRS
from shapely.geometry import Polygon, mapping

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

# Copernicus DEM GLO-30 is served via AWS Open Data Program
# Tiles are 1°×1° at 30 m resolution. We use the public S3 endpoint.
# URL pattern: s3://copernicus-dem-30m/Copernicus_DSM_COG_10_{lat}_{lon}_DEM/
# Public HTTP mirror via AWS S3:
COPERNICUS_DEM_BASE_URL = (
    "https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com"
)

SOURCE_NAME = "copernicus-dem-glo30"
RESOLUTION_M = 30
VERTICAL_REFERENCE = "EGM2008"
REQUEST_TIMEOUT_SECONDS = 60.0  # tile downloads can be slow

# Horn (1981) slope computation uses a 3×3 neighbourhood
# Pixel size in metres — GLO-30 is 1 arc-second ≈ 30 m at the equator
# We use the actual pixel size from the raster metadata when available.
_DEFAULT_PIXEL_SIZE_M = 30.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _tile_url(lat_floor: int, lon_floor: int) -> str:
    """Construct the Copernicus DEM GLO-30 tile URL for a 1°×1° tile.

    Tile naming convention:
      Copernicus_DSM_COG_10_N{lat:02d}_00_E{lon:03d}_00_DEM.tif
    where lat/lon are the south-west corner of the 1° tile in the
    hemisphere prefix format (N/S, E/W).

    Requirements: 6.1
    """
    lat_prefix = "N" if lat_floor >= 0 else "S"
    lon_prefix = "E" if lon_floor >= 0 else "W"
    lat_str = f"{abs(lat_floor):02d}"
    lon_str = f"{abs(lon_floor):03d}"
    tile_name = (
        f"Copernicus_DSM_COG_10_{lat_prefix}{lat_str}_00_{lon_prefix}{lon_str}_00_DEM"
    )
    return f"{COPERNICUS_DEM_BASE_URL}/{tile_name}/{tile_name}.tif"


def _required_tiles(polygon: Polygon) -> list[tuple[int, int]]:
    """Return the set of 1°×1° tile corners needed to cover a polygon's bounding box.

    Each tile is identified by its south-west corner (lat_floor, lon_floor).

    Requirements: 6.1
    """
    minx, miny, maxx, maxy = polygon.bounds
    lat_floors = range(math.floor(miny), math.ceil(maxy))
    lon_floors = range(math.floor(minx), math.ceil(maxx))
    return [(lat, lon) for lat in lat_floors for lon in lon_floors]


def compute_slope_horn(elevation: np.ndarray, pixel_size_m: float) -> np.ndarray:
    """Compute slope in degrees using the Horn (1981) finite difference method.

    For each interior pixel, the gradient is estimated using the 3×3 neighbourhood:
      dz/dx = ((z[r-1,c+1] + 2*z[r,c+1] + z[r+1,c+1]) -
               (z[r-1,c-1] + 2*z[r,c-1] + z[r+1,c-1])) / (8 * pixel_size)
      dz/dy = ((z[r+1,c-1] + 2*z[r+1,c] + z[r+1,c+1]) -
               (z[r-1,c-1] + 2*z[r-1,c] + z[r-1,c+1])) / (8 * pixel_size)
      slope = arctan(sqrt((dz/dx)² + (dz/dy)²)) in degrees

    Border pixels are set to NaN.

    Requirements: 6.2
    """
    rows, cols = elevation.shape
    slope = np.full((rows, cols), np.nan, dtype=np.float32)

    if rows < 3 or cols < 3:
        return slope

    # Using numpy slicing for vectorised Horn gradient
    z = elevation.astype(np.float32)

    # 3x3 kernel submatrices (avoid explicit loops)
    z_nw = z[:-2, :-2]   # row-1, col-1
    z_n  = z[:-2, 1:-1]  # row-1, col
    z_ne = z[:-2, 2:]    # row-1, col+1
    z_w  = z[1:-1, :-2]  # row,   col-1
    z_e  = z[1:-1, 2:]   # row,   col+1
    z_sw = z[2:, :-2]    # row+1, col-1
    z_s  = z[2:, 1:-1]   # row+1, col
    z_se = z[2:, 2:]     # row+1, col+1

    dz_dx = ((z_ne + 2 * z_e + z_se) - (z_nw + 2 * z_w + z_sw)) / (8 * pixel_size_m)
    dz_dy = ((z_sw + 2 * z_s + z_se) - (z_nw + 2 * z_n + z_ne)) / (8 * pixel_size_m)

    # Where either band is NaN (nodata), slope is NaN
    gradient_magnitude = np.sqrt(dz_dx ** 2 + dz_dy ** 2)
    slope_rad = np.arctan(gradient_magnitude)
    slope_deg = np.degrees(slope_rad).astype(np.float32)

    slope[1:-1, 1:-1] = slope_deg
    return slope


def _pixel_size_from_transform(transform: rasterio.transform.Affine) -> float:
    """Extract approximate pixel size in metres from a rasterio Affine transform.

    For a north-up raster the pixel width is |transform.a|.
    We convert from degrees to metres using a standard latitude approximation
    (1° ≈ 111320 m) only when the transform is in degrees; otherwise we use
    the raw value.

    For the Copernicus GLO-30 dataset in geographic CRS (EPSG:4326) the pixel
    size is 1/3600° ≈ 30.9 m at the equator. Using 30 m is a safe approximation.
    """
    pixel_width_deg = abs(transform.a)
    # If the transform is in degrees (< 1 implies geographic)
    if pixel_width_deg < 1.0:
        return pixel_width_deg * 111_320.0
    return pixel_width_deg


async def _download_tile(url: str, client: httpx.AsyncClient) -> bytes | None:
    """Download a DEM tile from a URL. Returns None on failure."""
    try:
        response = await client.get(
            url, timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True
        )
        if response.status_code == 404:
            # Tile may not exist (ocean or areas without coverage)
            logger.debug("DEM tile not found (likely ocean/no-data area): %s", url)
            return None
        response.raise_for_status()
        return response.content
    except httpx.HTTPStatusError as exc:
        logger.warning("Failed to download DEM tile %s: HTTP %s", url, exc.response.status_code)
        return None
    except Exception as exc:
        logger.warning("Failed to download DEM tile %s: %s", url, exc)
        return None


def _clip_dem_to_polygon(
    dem_bytes: bytes,
    farm_polygon: Polygon,
) -> tuple[np.ndarray | None, float]:
    """Open a DEM raster from bytes, clip to the farm polygon, and return the array.

    Returns (elevation_array, pixel_size_m) where elevation_array is 2D float32
    with nodata pixels set to NaN, or (None, default_pixel_size) on failure.

    Requirements: 6.1, 6.2
    """
    try:
        with rasterio.open(io.BytesIO(dem_bytes)) as src:
            pixel_size_m = _pixel_size_from_transform(src.transform)

            # If the DEM is in a projected CRS, reproject the polygon to match
            if src.crs and src.crs.to_epsg() != 4326:
                from pyproj import Transformer
                transformer = Transformer.from_crs(
                    "EPSG:4326", src.crs.to_epsg(), always_xy=True
                )
                coords = list(farm_polygon.exterior.coords)
                projected_coords = [
                    transformer.transform(lon, lat) for lon, lat in coords
                ]
                mask_polygon = Polygon(projected_coords)
            else:
                mask_polygon = farm_polygon

            clipped, _ = rasterio.mask.mask(
                src,
                [mapping(mask_polygon)],
                crop=True,
                nodata=src.nodata if src.nodata is not None else -9999,
                filled=True,
            )

            arr = clipped[0].astype(np.float32)

            # Replace nodata with NaN
            nodata_val = src.nodata if src.nodata is not None else -9999
            arr[arr == nodata_val] = np.nan
            # Also replace extreme sentinel values often used in DEMs
            arr[arr < -500] = np.nan   # below Dead Sea level
            arr[arr > 9000] = np.nan   # above Everest

            return arr, pixel_size_m
    except Exception as exc:
        logger.warning("Failed to clip DEM tile: %s", exc)
        return None, _DEFAULT_PIXEL_SIZE_M


def _compute_terrain_stats(
    elevation: np.ndarray,
    pixel_size_m: float,
) -> dict[str, float | None]:
    """Compute elevation and slope statistics from a clipped DEM array.

    Returns a dict with keys: mean_elevation_m, min_elevation_m,
    max_elevation_m, mean_slope_deg.

    Requirements: 6.2
    """
    valid_mask = ~np.isnan(elevation)
    valid_count = int(np.sum(valid_mask))

    if valid_count == 0:
        return {
            "mean_elevation_m": None,
            "min_elevation_m": None,
            "max_elevation_m": None,
            "mean_slope_deg": None,
        }

    valid_elev = elevation[valid_mask]
    mean_elev = float(np.mean(valid_elev))
    min_elev = float(np.min(valid_elev))
    max_elev = float(np.max(valid_elev))

    slope_array = compute_slope_horn(elevation, pixel_size_m)
    valid_slope = slope_array[~np.isnan(slope_array)]
    mean_slope = float(np.mean(valid_slope)) if valid_slope.size > 0 else None

    return {
        "mean_elevation_m": round(mean_elev, 2),
        "min_elevation_m": round(min_elev, 2),
        "max_elevation_m": round(max_elev, 2),
        "mean_slope_deg": round(mean_slope, 4) if mean_slope is not None else None,
    }


def _build_terrain_payload(
    stats: dict[str, float | None],
    retrieved_at: str,
    data_mode: str,
) -> dict:
    """Build the terrain payload from computed elevation/slope statistics.

    flood_probability is explicitly null — slope and elevation are not
    sufficient to infer flood exposure without drainage evidence.

    Requirements: 6.2, 6.3, 6.4
    """
    def _elev_envelope(value: float | None) -> dict:
        return provenance_envelope(
            value=value,
            unit="metres",
            source=SOURCE_NAME,
            acquired_at=retrieved_at,
            retrieved_at=retrieved_at,
            data_mode=data_mode,
            quality=EVIDENCE_ACCEPTED if value is not None else EVIDENCE_UNAVAILABLE,
            resolution_m=float(RESOLUTION_M),
        )

    def _slope_envelope(value: float | None) -> dict:
        return provenance_envelope(
            value=value,
            unit="degrees",
            source=SOURCE_NAME,
            acquired_at=retrieved_at,
            retrieved_at=retrieved_at,
            data_mode=data_mode,
            quality=EVIDENCE_ACCEPTED if value is not None else EVIDENCE_UNAVAILABLE,
            resolution_m=float(RESOLUTION_M),
        )

    return {
        "mean_elevation_m": _elev_envelope(stats["mean_elevation_m"]),
        "min_elevation_m": _elev_envelope(stats["min_elevation_m"]),
        "max_elevation_m": _elev_envelope(stats["max_elevation_m"]),
        "mean_slope_deg": _slope_envelope(stats["mean_slope_deg"]),
        # Requirements 6.4: explicitly null — do not infer flood probability
        "flood_probability": None,
        "flood_probability_note": (
            "Flood probability is not computed from slope and elevation alone. "
            "Drainage evidence is required for flood exposure assessment."
        ),
        "dem_source": SOURCE_NAME,
        "dem_resolution_m": RESOLUTION_M,
        "vertical_reference": VERTICAL_REFERENCE,
        "slope_method": "Horn (1981) 3×3 finite difference gradient",
        "retrieved_at": retrieved_at,
    }


def _build_unavailable_payload(retrieved_at: str, data_mode: str) -> dict:
    """Build a payload signalling terrain data is unavailable.

    Requirements: 6.5
    """
    def _null_elev() -> dict:
        return provenance_envelope(
            value=None,
            unit="metres",
            source=SOURCE_NAME,
            acquired_at=retrieved_at,
            retrieved_at=retrieved_at,
            data_mode=data_mode,
            quality=EVIDENCE_UNAVAILABLE,
            resolution_m=float(RESOLUTION_M),
        )

    def _null_slope() -> dict:
        return provenance_envelope(
            value=None,
            unit="degrees",
            source=SOURCE_NAME,
            acquired_at=retrieved_at,
            retrieved_at=retrieved_at,
            data_mode=data_mode,
            quality=EVIDENCE_UNAVAILABLE,
            resolution_m=float(RESOLUTION_M),
        )

    return {
        "mean_elevation_m": _null_elev(),
        "min_elevation_m": _null_elev(),
        "max_elevation_m": _null_elev(),
        "mean_slope_deg": _null_slope(),
        "flood_probability": None,
        "flood_probability_note": (
            "Flood probability is not computed from slope and elevation alone. "
            "Drainage evidence is required for flood exposure assessment."
        ),
        "dem_source": SOURCE_NAME,
        "dem_resolution_m": RESOLUTION_M,
        "vertical_reference": VERTICAL_REFERENCE,
        "slope_method": "Horn (1981) 3×3 finite difference gradient",
        "retrieved_at": retrieved_at,
    }


# ---------------------------------------------------------------------------
# Public adapter interface
# ---------------------------------------------------------------------------

async def fetch(
    farm_polygon: Polygon,
    data_mode: str = "live",
) -> ProviderResult:
    """Fetch terrain data from Copernicus DEM GLO-30 for the farm polygon.

    Downloads the required 1°×1° DEM tiles, merges them if multiple tiles
    are needed, clips to the farm polygon, and computes elevation and slope
    statistics.

    Args:
        farm_polygon: Shapely Polygon in WGS84 (EPSG:4326).
        data_mode: Provenance data mode label.

    Returns:
        ProviderResult with terrain payload and evidence_status="accepted"
        on success, or evidence_status="unavailable" when no tile data can
        be obtained.

    Never raises; all errors are captured.

    Requirements: 6.1, 6.2, 6.3, 6.4, 6.5
    """
    retrieved_at = _iso_now()
    tiles = _required_tiles(farm_polygon)

    if not tiles:
        msg = "Could not determine required DEM tiles for farm polygon."
        logger.warning(msg)
        return ProviderResult(
            payload=_build_unavailable_payload(retrieved_at, data_mode),
            evidence_status=EVIDENCE_UNAVAILABLE,
            error_message=msg,
        )

    async with httpx.AsyncClient() as client:
        # ------------------------------------------------------------------
        # Download all required tiles
        # ------------------------------------------------------------------
        tile_arrays: list[tuple[np.ndarray, float]] = []
        for lat_floor, lon_floor in tiles:
            url = _tile_url(lat_floor, lon_floor)
            tile_bytes = await _download_tile(url, client)
            if tile_bytes is None:
                continue
            arr, pixel_size_m = _clip_dem_to_polygon(tile_bytes, farm_polygon)
            if arr is not None and not np.all(np.isnan(arr)):
                tile_arrays.append((arr, pixel_size_m))

        if not tile_arrays:
            msg = (
                f"No valid DEM tile data found for farm polygon "
                f"(tried {len(tiles)} tile(s))."
            )
            logger.warning(msg)
            return ProviderResult(
                payload=_build_unavailable_payload(retrieved_at, data_mode),
                evidence_status=EVIDENCE_UNAVAILABLE,
                error_message=msg,
            )

        # ------------------------------------------------------------------
        # Merge tiles: if multiple tiles cover the polygon, concatenate
        # along the row axis (simple vertical stack for adjacent N-S tiles).
        # For typical farm sizes one tile will suffice; multi-tile farms
        # that span a degree boundary are merged naively — sufficient for
        # mean/min/max statistics.
        # ------------------------------------------------------------------
        if len(tile_arrays) == 1:
            elevation, pixel_size_m = tile_arrays[0]
        else:
            # Use the pixel size from the first tile (all tiles have same res)
            pixel_size_m = tile_arrays[0][1]
            # Pad arrays to the same column width before vstack
            max_cols = max(a.shape[1] for a, _ in tile_arrays)
            padded = []
            for a, _ in tile_arrays:
                if a.shape[1] < max_cols:
                    pad_width = max_cols - a.shape[1]
                    a = np.pad(
                        a,
                        ((0, 0), (0, pad_width)),
                        constant_values=np.nan,
                    )
                padded.append(a)
            elevation = np.vstack(padded)

        # ------------------------------------------------------------------
        # Compute terrain statistics
        # ------------------------------------------------------------------
        stats = _compute_terrain_stats(elevation, pixel_size_m)

        # If all statistics are None the polygon had no valid elevation data
        if all(v is None for v in stats.values()):
            msg = "DEM tiles downloaded but no valid elevation pixels within farm polygon."
            logger.warning(msg)
            return ProviderResult(
                payload=_build_unavailable_payload(retrieved_at, data_mode),
                evidence_status=EVIDENCE_UNAVAILABLE,
                error_message=msg,
            )

        payload = _build_terrain_payload(stats, retrieved_at, data_mode)
        return ProviderResult(
            payload=payload,
            evidence_status=EVIDENCE_ACCEPTED,
            error_message=None,
        )
