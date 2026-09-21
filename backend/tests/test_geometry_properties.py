"""Property and edge-case tests for authoritative farm geometry validation."""

import pytest
from hypothesis import given, strategies as st
from pyproj import Geod
from shapely.geometry import Polygon, Point

from app.domain.geometry import (
    GEOMETRY_AREA_EXCEEDED,
    GEOMETRY_DEGENERATE,
    GEOMETRY_INVALID,
    GEOMETRY_TOO_FEW_VERTICES,
    GEOMETRY_TOO_SMALL,
    GEOMETRY_TOPOLOGY_ERROR,
    validate_polygon,
)


def polygon(points):
    return {"type": "Polygon", "coordinates": [points]}


@pytest.mark.parametrize(
    ("geometry", "code"),
    [
        ({"type": "LineString", "coordinates": []}, GEOMETRY_INVALID),
        (polygon([[181, 0], [0, 0], [0, 1], [181, 0]]), GEOMETRY_INVALID),
        (polygon([[0, 0], [1, 0], [0, 0]]), GEOMETRY_TOO_FEW_VERTICES),
        (polygon([[0, 0], [1, 1], [0, 1], [1, 0], [0, 0]]), GEOMETRY_TOPOLOGY_ERROR),
        (polygon([[0, 0], [1, 1], [2, 2], [0, 0]]), GEOMETRY_DEGENERATE),
        (polygon([[0, 0], [20, 0], [20, 20], [0, 0]]), GEOMETRY_AREA_EXCEEDED),
        (polygon([[0, 0], [0.000001, 0], [0, 0.000001], [0, 0]]), GEOMETRY_TOO_SMALL),
    ],
)
def test_validator_edge_codes(geometry, code):
    result = validate_polygon(geometry)
    assert result.valid is False
    assert result.error_code == code


@given(
    lon=st.floats(min_value=30, max_value=40, allow_nan=False, allow_infinity=False),
    lat=st.floats(min_value=-4, max_value=4, allow_nan=False, allow_infinity=False),
    span=st.floats(min_value=0.001, max_value=0.02, allow_nan=False, allow_infinity=False),
)
def test_property_4_geodesic_area_matches_pyproj(lon, lat, span):
    geometry = polygon(
        [[lon, lat], [lon + span, lat], [lon + span, lat + span], [lon, lat]]
    )
    result = validate_polygon(geometry)
    assert result.valid
    expected, _ = Geod(ellps="WGS84").geometry_area_perimeter(
        Polygon(geometry["coordinates"][0])
    )
    assert result.area_ha == pytest.approx(abs(expected) / 10_000, abs=1e-4)


def test_property_5_concave_label_point_is_inside():
    geometry = polygon(
        [[0, 0], [4, 0], [4, 1], [1, 1], [1, 4], [0, 4], [0, 0]]
    )
    result = validate_polygon(geometry, max_area_ha=10_000_000)
    assert result.valid
    assert Polygon(geometry["coordinates"][0]).contains(
        Point(result.label_point_lon, result.label_point_lat)
    )
