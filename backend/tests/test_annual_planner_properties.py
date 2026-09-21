"""
Property-based tests for the Annual Planner service (Phase 8).

Feature: annual-planner

Property 2: Overlapping entries are rejected
Validates: Requirements 2.2, 2.3, 3.3
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from app.api.v1.farm_schemas import FarmCreate
from app.core.exceptions import FarmValidationError
from app.core.security import Principal
from app.domain.crop_register import CROP_REGISTER
from app.models.user import User
from app.services.farm_service import FarmService
from app.services.planner_service import PlanEntryCreate, create_entry


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

FARM_BOUNDARY = {
    "type": "Polygon",
    "coordinates": [
        [[36.80, -1.30], [36.805, -1.30], [36.805, -1.295], [36.80, -1.30]]
    ],
}

# Crop names present in the register (used to generate valid crop names)
_CROP_NAMES = [c.name for c in CROP_REGISTER]

# Maximum duration_months across all crops in the register
_MAX_DURATION = max(c.duration_months for c in CROP_REGISTER)


async def _create_user(session_factory, subject: str) -> User:
    async with session_factory() as session:
        user = User(
            id=uuid.uuid4(),
            issuer="annual-planner-property-tests",
            subject=subject,
            email=f"{subject}@example.test",
            display_name=subject,
        )
        session.add(user)
        await session.commit()
        return user


async def _create_farm(session_factory, user: User):
    async with session_factory() as session:
        service = FarmService(session, user.id)
        result = await service.create_farm_request(
            FarmCreate(name="Planner Test Farm", geometry=FARM_BOUNDARY)
        )
        return result.farm


def _make_principal(user_id: uuid.UUID) -> Principal:
    return Principal(
        user_id=user_id,
        issuer="annual-planner-property-tests",
        subject=str(user_id),
        permissions=frozenset(),
    )


# ---------------------------------------------------------------------------
# Property 2: Overlapping entries are rejected
#
# For any two Plan_Entries whose [planting_date, harvest_date] intervals
# overlap on the same farm, attempting to create the second must return a
# 422 with the conflicting entry identified.
#
# Strategy: pick a first crop and planting date, create it, then derive its
# harvest window.  Pick a second planting date guaranteed to fall within that
# window, then try to create a second entry — must raise FarmValidationError.
#
# Feature: annual-planner, Property 2: Overlapping entries are rejected
# Validates: Requirements 2.2, 2.3, 3.3
# ---------------------------------------------------------------------------

# Use a fixed crop with a known duration so we can construct overlapping dates
_FIXED_CROP = "Maize"  # 4-month duration
_FIXED_DURATION = next(c.duration_months for c in CROP_REGISTER if c.name == _FIXED_CROP)


@given(
    # First entry planting date: years 2027–2028, any month, day 1
    first_month=st.integers(min_value=1, max_value=12),
    first_year=st.integers(min_value=2027, max_value=2028),
    # Overlap offset: how many days into the first entry's window to start the second
    overlap_offset_days=st.integers(min_value=1, max_value=(_FIXED_DURATION * 28) - 1),
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
@pytest.mark.asyncio
async def test_property_2_overlapping_entries_rejected(
    session_factory,
    first_month: int,
    first_year: int,
    overlap_offset_days: int,
) -> None:
    """For any two Plan_Entries with overlapping [planting_date, harvest_date]
    windows on the same farm, creating the second must raise FarmValidationError (→ 422).

    Feature: annual-planner, Property 2: Overlapping entries are rejected
    Validates: Requirements 2.2, 2.3, 3.3
    """
    # Create a fresh user+farm for each example to avoid cross-test contamination
    subject = f"overlap-prop2-{uuid.uuid4().hex[:8]}"
    user = await _create_user(session_factory, subject)
    farm = await _create_farm(session_factory, user)
    principal = _make_principal(user.id)

    first_planting = date(first_year, first_month, 1)

    # Create the first entry
    async with session_factory() as session:
        first_entry = await create_entry(
            session=session,
            principal=principal,
            farm_id=farm.id,
            data=PlanEntryCreate(
                crop_name=_FIXED_CROP,
                planting_date=first_planting,
                cultivation_mode="rainfed",
                irrigation_mm=None,
                area_ha=1.0,
            ),
        )

    # The second planting date falls inside the first entry's window
    second_planting = date.fromordinal(
        first_planting.toordinal() + overlap_offset_days
    )

    # Attempting to create the second entry must raise FarmValidationError
    with pytest.raises(FarmValidationError) as exc_info:
        async with session_factory() as session:
            await create_entry(
                session=session,
                principal=principal,
                farm_id=farm.id,
                data=PlanEntryCreate(
                    crop_name=_FIXED_CROP,
                    planting_date=second_planting,
                    cultivation_mode="rainfed",
                    irrigation_mm=None,
                    area_ha=1.0,
                ),
            )

    # The error must identify the conflict (Req 2.3)
    assert exc_info.value.details, "Expected error details with conflicting entry info"
    detail = exc_info.value.details[0]
    assert detail.code == "OVERLAP_CONFLICT", (
        f"Expected OVERLAP_CONFLICT error code, got {detail.code!r}"
    )
    assert str(first_entry.id) in detail.message, (
        f"Expected conflicting entry id {first_entry.id} in error message: {detail.message!r}"
    )
