"""
Property-based tests for the satellite adapter.

Feature: environmental-twin, Property 3: NDVI formula correctness
Validates: Requirements 4.2
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings, strategies as st

from app.data.providers.satellite import _NDVI_EPS, compute_ndvi


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

# Reflectance-like pixel values: non-negative, up to ~10000 (Sentinel-2 DN)
_pixel_value = st.floats(min_value=0.0, max_value=10000.0, allow_nan=False, allow_infinity=False)


@st.composite
def b8_b4_pairs_nonzero_sum(draw):
    """Generate (B8, B4) scalar pairs where B8 + B4 > 0 after float32 casting.

    The constraint is evaluated in float32 to match what compute_ndvi sees.
    This avoids subnormal float64 values (e.g. 3e-230) that underflow to 0.0
    when cast to float32, which would incorrectly trigger the nodata mask.
    """
    b4 = draw(_pixel_value)
    b8 = draw(st.floats(min_value=0.0, max_value=10000.0, allow_nan=False, allow_infinity=False))
    # Evaluate the nodata condition in float32, matching compute_ndvi's casting
    b8_f32 = np.float32(b8)
    b4_f32 = np.float32(b4)
    if b8_f32 == np.float32(0.0) and b4_f32 == np.float32(0.0):
        b8 = 1.0
    return b8, b4


# ---------------------------------------------------------------------------
# Property 3: NDVI formula correctness
# Feature: environmental-twin, Property 3: NDVI formula correctness
# Validates: Requirements 4.2
# ---------------------------------------------------------------------------

@given(pair=b8_b4_pairs_nonzero_sum())
@settings(max_examples=200)
def test_property_3_ndvi_formula_correctness(pair):
    """For any (B8, B4) pixel pair where B8 + B4 > 0:
    - computed NDVI must equal (B8 - B4) / (B8 + B4) within 1e-9 tolerance
    - result must be in [-1, 1]

    Feature: environmental-twin, Property 3: NDVI formula correctness
    Validates: Requirements 4.2
    """
    b8_val, b4_val = pair

    b8 = np.array([[b8_val]], dtype=np.float32)
    b4 = np.array([[b4_val]], dtype=np.float32)

    result = compute_ndvi(b8, b4)
    ndvi = float(result[0, 0])

    # Must not be NaN (B8 + B4 > 0 means it is not a nodata pixel)
    assert not np.isnan(ndvi), f"NDVI should not be NaN when B8+B4 > 0 (b8={b8_val}, b4={b4_val})"

    # Must match the formula: (B8 - B4) / (B8 + B4 + ε)
    # Tolerance is 1e-6 (not 1e-9) because compute_ndvi works in float32,
    # which has ~7 significant digits of precision. Comparing against a
    # float64 reference with tighter tolerance would produce false failures.
    b8_f32 = float(np.float32(b8_val))
    b4_f32 = float(np.float32(b4_val))
    expected = (b8_f32 - b4_f32) / (b8_f32 + b4_f32 + _NDVI_EPS)
    assert abs(ndvi - expected) < 1e-6, (
        f"NDVI={ndvi} does not match formula value {expected} "
        f"(b8={b8_val}, b4={b4_val})"
    )

    # Must be in [-1, 1]
    assert -1.0 <= ndvi <= 1.0, (
        f"NDVI={ndvi} is outside [-1, 1] (b8={b8_val}, b4={b4_val})"
    )
