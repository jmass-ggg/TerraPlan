"""
Geometry validator for farm boundary polygons.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9

This module is a pure Python domain component with no framework or database
dependencies. It validates GeoJSON Polygon dicts and computes derived
spatial properties (area, centroid, label point).
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from pyproj import Geod
from shapely import from_geojson
from shapely.geometry import Polygon, mapping
from shapely.validation import explain_validity
import json


# ---------------------------------------------------------------------------
# Error codes (stable contract for API consumers)
# ---------------------------------------------------------------------------

GEOMETRY_INVALID = "GEOMETRY_INVALID"
GEOMETRY_TOO_FEW_VERTICES = "GEOMETRY_TOO_FEW_VERTICES"
GEOMETRY_TOPOLOGY_ERROR = "GEOMETRY_TOPOLOGY_ERROR"
GEOMETRY_DEGENERATE = "GEOMETRY_DEGENERATE"
GEOMETRY_AREA_EXCEEDED = "GEOMETRY_AREA_EXCEEDED"
GEOMETRY_TOO_SMALL = "GEOMETRY_TOO_SMALL"

# Minimum area in hectares (Requirement 3.7)
MIN_AREA_HA: float = 0.01

# Default maximum area in hectares (Requirement 3.6)
DEFAULT_MAX_AREA_HA: float = 50_000.0


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ValidationResult:
    """
    Result of polygon validation.

    On failure: valid=False, error_code and error_detail are set; all spatial
    fields are None.
    On success: valid=True, error_code and error_detail are None; all spatial
    fields are populated.
    """

    valid: bool

    # Populated only on failure
    error_code: str | None
    error_detail: str | None

    # Populated only on success
    area_ha: float | None
    centroid_lon: float | None
    centroid_lat: float | None
    label_point_lon: float | None
    label_point_lat: float | None


def _fail(code: str, detail: str) -> ValidationResult:
    return ValidationResult(
        valid=False,
        error_code=code,
        error_detail=detail,
        area_ha=None,
        centroid_lon=None,
        centroid_lat=None,
        label_point_lon=None,
        label_point_lat=None,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_polygon(
    geojson_geometry: dict,
    max_area_ha: float = DEFAULT_MAX_AREA_HA,
) -> ValidationResult:
    """
    Validate a GeoJSON Polygon dict and compute spatial derived properties.

    Validation sequence (stops at first failure):
    1. type == "Polygon" check
    2. Coordinate range validation: lon ∈ [-180, 180], lat ∈ [-90, 90]
    3. Minimum coordinate pairs: >= 4 (3 distinct + closure)
    4. Shapely topology: is_valid check
    5. Collinear / degenerate check via Shapely simplification
    6. Geodesic area bounds: [MIN_AREA_HA, max_area_ha]

    On success also computes:
    - area_ha via PyProj Geod WGS84
    - centroid (lon, lat)
    - label_point: centroid if inside polygon, else representative_point()

    Args:
        geojson_geometry: A dict expected to be a GeoJSON Polygon.
        max_area_ha: Maximum allowed area in hectares (default 50,000).

    Returns:
        ValidationResult with valid=True and populated spatial fields on
        success, or valid=False with error_code and error_detail on failure.

    Never raises on validation failure. May raise TypeError/KeyError only
    when geojson_geometry is not a dict or is structurally malformed beyond
    the type key.
    """
    # -----------------------------------------------------------------------
    # Step 1: type check (Requirement 3.1)
    # -----------------------------------------------------------------------
    if not isinstance(geojson_geometry, dict):
        return _fail(GEOMETRY_INVALID, "Geometry must be a GeoJSON object")

    geo_type = geojson_geometry.get("type")
    if geo_type != "Polygon":
        return _fail(
            GEOMETRY_INVALID,
            f"Expected GeoJSON type 'Polygon', got {geo_type!r}",
        )

    coordinates = geojson_geometry.get("coordinates")
    if not coordinates or not isinstance(coordinates, list):
        return _fail(GEOMETRY_INVALID, "Missing or empty 'coordinates' array")

    # Work with the exterior ring only (holes / multipolygons are Phase 5+)
    exterior: list = coordinates[0] if coordinates else []
    if not exterior or not isinstance(exterior, list):
        return _fail(GEOMETRY_INVALID, "Exterior ring is missing or empty")

    # -----------------------------------------------------------------------
    # Step 2: coordinate range validation (Requirement 3.2)
    # -----------------------------------------------------------------------
    for pair in exterior:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            return _fail(GEOMETRY_INVALID, "Each coordinate must be [lon, lat]")
        lon, lat = pair[0], pair[1]
        if (
            isinstance(lon, bool)
            or isinstance(lat, bool)
            or not isinstance(lon, (int, float))
            or not isinstance(lat, (int, float))
            or not math.isfinite(float(lon))
            or not math.isfinite(float(lat))
        ):
            return _fail(GEOMETRY_INVALID, "Coordinates must be finite numbers")
        if not (-180 <= lon <= 180):
            return _fail(
                GEOMETRY_INVALID,
                f"Longitude {lon} is outside [-180, 180]",
            )
        if not (-90 <= lat <= 90):
            return _fail(
                GEOMETRY_INVALID,
                f"Latitude {lat} is outside [-90, 90]",
            )

    # -----------------------------------------------------------------------
    # Step 3: minimum coordinate pairs (Requirement 3.3)
    # 4 pairs = 3 distinct vertices + 1 closure
    # -----------------------------------------------------------------------
    if len(exterior) < 4:
        return _fail(
            GEOMETRY_TOO_FEW_VERTICES,
            f"Polygon ring has {len(exterior)} coordinate pairs; "
            "minimum is 4 (3 distinct vertices + closure)",
        )

    if exterior[0][:2] != exterior[-1][:2]:
        return _fail(GEOMETRY_INVALID, "Polygon ring must be closed")

    if len({(pair[0], pair[1]) for pair in exterior[:-1]}) < 3:
        return _fail(
            GEOMETRY_TOO_FEW_VERTICES,
            "Polygon must contain at least 3 distinct vertices",
        )

    # -----------------------------------------------------------------------
    # Step 4: Shapely topology (Requirement 3.4)
    # -----------------------------------------------------------------------
    try:
        shapely_polygon: Polygon = Polygon(exterior)
    except Exception as exc:
        return _fail(GEOMETRY_TOPOLOGY_ERROR, f"Could not construct polygon: {exc}")

    distinct = list(dict.fromkeys((pair[0], pair[1]) for pair in exterior[:-1]))
    anchor_x, anchor_y = distinct[0]
    vector_x = distinct[1][0] - anchor_x
    vector_y = distinct[1][1] - anchor_y
    is_collinear = all(
        abs(vector_x * (y - anchor_y) - vector_y * (x - anchor_x)) < 1e-12
        for x, y in distinct[2:]
    )
    if is_collinear:
        return _fail(
            GEOMETRY_DEGENERATE,
            "Polygon is degenerate (all vertices are collinear or coincident)",
        )

    if not shapely_polygon.is_valid:
        reason = explain_validity(shapely_polygon)
        return _fail(GEOMETRY_TOPOLOGY_ERROR, reason)

    # -----------------------------------------------------------------------
    # Step 5: collinear / degenerate check (Requirement 3.5)
    # Simplify with tolerance=0 removes collinear points; if the ring shrinks
    # below 4 coords (3 distinct + closure), the polygon is degenerate.
    # -----------------------------------------------------------------------
    simplified = shapely_polygon.simplify(0, preserve_topology=False)
    # A degenerate polygon simplifies to a point, line, or empty geometry
    if simplified.is_empty or simplified.geom_type != "Polygon":
        return _fail(
            GEOMETRY_DEGENERATE,
            "Polygon is degenerate (all vertices are collinear or coincident)",
        )
    simplified_ring = list(simplified.exterior.coords)
    if len(simplified_ring) < 4:
        return _fail(
            GEOMETRY_DEGENERATE,
            "Polygon is degenerate after removing collinear vertices",
        )

    # -----------------------------------------------------------------------
    # Step 6: geodesic area (Requirements 3.6, 3.7, 3.8)
    # PyProj Geod returns area in m², convert to hectares.
    # geometry_area_perimeter returns (area, perimeter); area may be negative
    # (depending on ring orientation) so we take the absolute value.
    # -----------------------------------------------------------------------
    geod = Geod(ellps="WGS84")
    area_m2, _ = geod.geometry_area_perimeter(shapely_polygon)
    area_ha = abs(area_m2) / 10_000.0

    if area_ha > max_area_ha:
        return _fail(
            GEOMETRY_AREA_EXCEEDED,
            f"Geodesic area {area_ha:.4f} ha exceeds maximum {max_area_ha} ha",
        )
    if area_ha < MIN_AREA_HA:
        return _fail(
            GEOMETRY_TOO_SMALL,
            f"Geodesic area {area_ha:.6f} ha is below minimum {MIN_AREA_HA} ha",
        )

    # -----------------------------------------------------------------------
    # Derive centroid and label point (Requirement 3.9)
    # -----------------------------------------------------------------------
    centroid = shapely_polygon.centroid
    centroid_lon, centroid_lat = centroid.x, centroid.y

    if shapely_polygon.contains(centroid):
        label_lon, label_lat = centroid_lon, centroid_lat
    else:
        # Centroid falls outside concave polygon; use guaranteed interior point
        rep = shapely_polygon.representative_point()
        label_lon, label_lat = rep.x, rep.y

    return ValidationResult(
        valid=True,
        error_code=None,
        error_detail=None,
        area_ha=area_ha,
        centroid_lon=centroid_lon,
        centroid_lat=centroid_lat,
        label_point_lon=label_lon,
        label_point_lat=label_lat,
    )
