"""
Property-based tests for Conduit eligibility monotonicity.

Feature: environmental-twin, Property 5: Conduit eligibility monotonicity
Validates: Requirements 9.1, 9.2

The distance-based eligibility rule is a hard threshold: if a farm at distance
d2 is eligible, then any farm at distance d1 < d2 must also be eligible.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st
from pyproj import Geod
from shapely.geometry import Point

from app.data.providers.conduit_eligibility import (
    DISTANCE_THRESHOLD_KM,
    _geodesic_distance_km,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_distance_eligible(distance_km: float) -> bool:
    """Mirror of the distance eligibility check in conduit_eligibility.fetch."""
    return distance_km <= DISTANCE_THRESHOLD_KM


def _place_farm_at_km(station_lat: float, station_lon: float, distance_km: float) -> Point:
    """Return a farm centroid Point that is exactly *distance_km* km north of
    the station.  Using a simple northward offset keeps the geometry valid
    while giving us a deterministic distance that is easy to reason about.
    """
    geod = Geod(ellps="WGS84")
    # az=0 → due north; distance in metres
    lon2, lat2, _ = geod.fwd(station_lon, station_lat, az=0, dist=distance_km * 1000.0)
    return Point(lon2, lat2)


# ---------------------------------------------------------------------------
# Property 5: Conduit eligibility monotonicity
# Feature: environmental-twin, Property 5: Conduit eligibility monotonicity
# Validates: Requirements 9.1, 9.2
# ---------------------------------------------------------------------------

@given(
    station_lat=st.floats(min_value=-60.0, max_value=60.0, allow_nan=False, allow_infinity=False),
    station_lon=st.floats(min_value=-170.0, max_value=170.0, allow_nan=False, allow_infinity=False),
    d1_km=st.floats(min_value=0.0, max_value=DISTANCE_THRESHOLD_KM, allow_nan=False, allow_infinity=False),
    d2_km=st.floats(min_value=0.0, max_value=DISTANCE_THRESHOLD_KM * 2, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=200)
def test_property_5_conduit_eligibility_monotonicity(
    station_lat: float,
    station_lon: float,
    d1_km: float,
    d2_km: float,
):
    """For any two farms where farm A is closer to the station than farm B,
    if farm B is eligible (distance <= threshold) then farm A must also be
    eligible.

    Feature: environmental-twin, Property 5: Conduit eligibility monotonicity
    Validates: Requirements 9.1, 9.2
    """
    # Place farm A at d1_km and farm B at d2_km (both due north of station)
    farm_a = _place_farm_at_km(station_lat, station_lon, d1_km)
    farm_b = _place_farm_at_km(station_lat, station_lon, d2_km)

    # Compute actual geodesic distances back (round-trip sanity check)
    dist_a = _geodesic_distance_km(farm_a.y, farm_a.x, station_lat, station_lon)
    dist_b = _geodesic_distance_km(farm_b.y, farm_b.x, station_lat, station_lon)

    eligible_a = _is_distance_eligible(dist_a)
    eligible_b = _is_distance_eligible(dist_b)

    # Monotonicity: if d1 <= d2, then eligible(d2) => eligible(d1)
    if d1_km <= d2_km and eligible_b:
        assert eligible_a, (
            f"Monotonicity violated: farm_b at {dist_b:.3f} km is eligible "
            f"but farm_a at {dist_a:.3f} km (closer) is not. "
            f"Threshold: {DISTANCE_THRESHOLD_KM} km."
        )
