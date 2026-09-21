"""
Integration tests for the Annual Planner (Phase 8 checkpoint).

Verifies backend behaviour with a real database:
- Entry creation and persistence across sessions
- Overlap validation (two conflicting entries → 422 on second)
- Cross-year harvest: planting in November → harvest in following year
- Cross-user isolation: user A cannot see or modify user B's entries

Requirements: 2.2, 3.2, 3.3
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.api.v1.farm_schemas import FarmCreate
from app.core.exceptions import FarmValidationError, NotFoundError
from app.core.security import Principal
from app.models.plan import PlanEntry, ChangeProposal, ProposalStatus
from app.models.user import User
from app.services.farm_service import FarmService
from app.services.planner_service import (
    PlanEntryCreate,
    PlanEntryUpdate,
    accept_proposal,
    create_entry,
    delete_entry,
    get_annual_plan,
    update_entry,
)
from sqlalchemy import select


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FARM_BOUNDARY = {
    "type": "Polygon",
    "coordinates": [
        [[36.80, -1.30], [36.805, -1.30], [36.805, -1.295], [36.80, -1.30]]
    ],
}


async def _create_user(session_factory, subject: str) -> User:
    async with session_factory() as session:
        user = User(
            id=uuid.uuid4(),
            issuer="annual-planner-integration",
            subject=subject,
            email=f"{subject}@example.test",
            display_name=subject,
        )
        session.add(user)
        await session.commit()
        return user


async def _create_farm(session_factory, user: User):
    async with session_factory() as session:
        result = await FarmService(session, user.id).create_farm_request(
            FarmCreate(name="Planner Integration Farm", geometry=FARM_BOUNDARY)
        )
        return result.farm


def _principal(user: User) -> Principal:
    return Principal(
        user_id=user.id,
        issuer="annual-planner-integration",
        subject=str(user.id),
        permissions=frozenset(),
    )


# ---------------------------------------------------------------------------
# 1. Entry creation and persistence across sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entry_persists_across_sessions(session_factory):
    """Create an entry in one session and reload it in a fresh session.

    Requirements: 2.1, 3.1
    """
    user = await _create_user(session_factory, f"persist-{uuid.uuid4().hex[:6]}")
    farm = await _create_farm(session_factory, user)
    principal = _principal(user)

    # Create entry
    async with session_factory() as session:
        entry = await create_entry(
            session=session,
            principal=principal,
            farm_id=farm.id,
            data=PlanEntryCreate(
                crop_name="Maize",
                planting_date=date(2027, 3, 1),
                cultivation_mode="rainfed",
                irrigation_mm=None,
                area_ha=2.0,
            ),
        )
        entry_id = entry.id
        saved_harvest = entry.harvest_date

    # Maize has duration_months=4: harvest should be 2027-07-01
    assert saved_harvest == date(2027, 7, 1)

    # Reload in a brand new session
    async with session_factory() as session:
        result = await session.execute(
            select(PlanEntry).where(PlanEntry.id == entry_id)
        )
        loaded = result.scalar_one_or_none()

    assert loaded is not None, "Entry not found after session close"
    assert loaded.crop_name == "Maize"
    assert loaded.planting_date == date(2027, 3, 1)
    assert loaded.harvest_date == date(2027, 7, 1)
    assert loaded.area_ha == pytest.approx(2.0)
    assert loaded.cultivation_mode == "rainfed"
    assert loaded.farm_id == farm.id


# ---------------------------------------------------------------------------
# 2. Overlap: two conflicting entries → 422 on second
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overlap_second_entry_rejected(session_factory):
    """Two entries with overlapping date windows → second create raises FarmValidationError.

    Requirements: 2.2, 2.3, 3.3
    """
    user = await _create_user(session_factory, f"overlap-{uuid.uuid4().hex[:6]}")
    farm = await _create_farm(session_factory, user)
    principal = _principal(user)

    # First entry: Maize, March 1 → July 1 (4 months)
    async with session_factory() as session:
        first = await create_entry(
            session=session,
            principal=principal,
            farm_id=farm.id,
            data=PlanEntryCreate(
                crop_name="Maize",
                planting_date=date(2027, 3, 1),
                cultivation_mode="rainfed",
                irrigation_mm=None,
                area_ha=1.0,
            ),
        )

    # Second entry: starts May 1 — inside first entry's window (March–July)
    with pytest.raises(FarmValidationError) as exc_info:
        async with session_factory() as session:
            await create_entry(
                session=session,
                principal=principal,
                farm_id=farm.id,
                data=PlanEntryCreate(
                    crop_name="Maize",
                    planting_date=date(2027, 5, 1),
                    cultivation_mode="rainfed",
                    irrigation_mm=None,
                    area_ha=1.0,
                ),
            )

    assert exc_info.value.details, "Expected OVERLAP_CONFLICT error details"
    detail = exc_info.value.details[0]
    assert detail.code == "OVERLAP_CONFLICT"
    assert str(first.id) in detail.message


@pytest.mark.asyncio
async def test_non_overlapping_entries_succeed(session_factory):
    """Two entries that don't overlap should both be created without error.

    Requirements: 3.3
    """
    user = await _create_user(session_factory, f"no-overlap-{uuid.uuid4().hex[:6]}")
    farm = await _create_farm(session_factory, user)
    principal = _principal(user)

    # Beans: 3-month duration → March 1 to June 1
    async with session_factory() as session:
        first = await create_entry(
            session=session,
            principal=principal,
            farm_id=farm.id,
            data=PlanEntryCreate(
                crop_name="Beans",
                planting_date=date(2027, 3, 1),
                cultivation_mode="rainfed",
                irrigation_mm=None,
                area_ha=1.0,
            ),
        )

    # Maize: starts June 1 — immediately after Beans harvest (no overlap)
    async with session_factory() as session:
        second = await create_entry(
            session=session,
            principal=principal,
            farm_id=farm.id,
            data=PlanEntryCreate(
                crop_name="Maize",
                planting_date=date(2027, 6, 1),
                cultivation_mode="rainfed",
                irrigation_mm=None,
                area_ha=1.0,
            ),
        )

    assert first.harvest_date == date(2027, 6, 1)
    assert second.planting_date == date(2027, 6, 1)
    # Both entries should exist
    async with session_factory() as session:
        result = await session.execute(
            select(PlanEntry).where(PlanEntry.farm_id == farm.id)
        )
        entries = result.scalars().all()
    assert len(entries) == 2


# ---------------------------------------------------------------------------
# 3. Cross-year harvest: planting in November → harvest in following year
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_year_harvest_stored_correctly(session_factory):
    """A crop planted in November should have its harvest date in the following year.

    Maize has duration_months=4: Nov 1 + 4 months = Mar 1 next year.

    Requirements: 3.1, 3.2
    """
    user = await _create_user(session_factory, f"crossyear-{uuid.uuid4().hex[:6]}")
    farm = await _create_farm(session_factory, user)
    principal = _principal(user)

    async with session_factory() as session:
        entry = await create_entry(
            session=session,
            principal=principal,
            farm_id=farm.id,
            data=PlanEntryCreate(
                crop_name="Maize",
                planting_date=date(2027, 11, 1),
                cultivation_mode="rainfed",
                irrigation_mm=None,
                area_ha=1.5,
            ),
        )
        entry_id = entry.id

    # 2027-11-01 + 4 months = 2028-03-01
    assert entry.planting_date.year == 2027
    assert entry.harvest_date.year == 2028
    assert entry.harvest_date == date(2028, 3, 1)

    # Reload and verify persistence
    async with session_factory() as session:
        result = await session.execute(
            select(PlanEntry).where(PlanEntry.id == entry_id)
        )
        loaded = result.scalar_one_or_none()

    assert loaded is not None
    assert loaded.planting_date == date(2027, 11, 1)
    assert loaded.harvest_date == date(2028, 3, 1)


@pytest.mark.asyncio
async def test_cross_year_harvest_december_planting(session_factory):
    """Crop planted in December harvests in the following year.

    Beans has duration_months=3: Dec 1 + 3 months = Mar 1 next year.

    Requirements: 3.2
    """
    user = await _create_user(session_factory, f"dec-{uuid.uuid4().hex[:6]}")
    farm = await _create_farm(session_factory, user)
    principal = _principal(user)

    async with session_factory() as session:
        entry = await create_entry(
            session=session,
            principal=principal,
            farm_id=farm.id,
            data=PlanEntryCreate(
                crop_name="Beans",
                planting_date=date(2027, 12, 1),
                cultivation_mode="rainfed",
                irrigation_mm=None,
                area_ha=1.0,
            ),
        )

    # 2027-12-01 + 3 months = 2028-03-01
    assert entry.harvest_date == date(2028, 3, 1)
    assert entry.harvest_date.year == 2028


# ---------------------------------------------------------------------------
# 4. Cross-user isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_user_isolation_cannot_read_other_farm(session_factory):
    """User B cannot see entries belonging to User A's farm.

    Requirements: 2.4 (ownership-scoped GET)
    """
    user_a = await _create_user(session_factory, f"user-a-{uuid.uuid4().hex[:6]}")
    user_b = await _create_user(session_factory, f"user-b-{uuid.uuid4().hex[:6]}")

    farm_a = await _create_farm(session_factory, user_a)
    principal_a = _principal(user_a)
    principal_b = _principal(user_b)

    # User A creates an entry
    async with session_factory() as session:
        await create_entry(
            session=session,
            principal=principal_a,
            farm_id=farm_a.id,
            data=PlanEntryCreate(
                crop_name="Maize",
                planting_date=date(2027, 3, 1),
                cultivation_mode="rainfed",
                irrigation_mm=None,
                area_ha=1.0,
            ),
        )

    # User B attempts to GET user A's crop plan → NotFoundError (wrong owner)
    with pytest.raises(NotFoundError):
        async with session_factory() as session:
            await get_annual_plan(
                session=session,
                principal=principal_b,
                farm_id=farm_a.id,
                year=2027,
            )


@pytest.mark.asyncio
async def test_cross_user_isolation_cannot_delete_other_entry(session_factory):
    """User B cannot delete an entry that belongs to User A.

    Requirements: 2.6 (ownership-scoped delete)
    """
    user_a = await _create_user(session_factory, f"del-a-{uuid.uuid4().hex[:6]}")
    user_b = await _create_user(session_factory, f"del-b-{uuid.uuid4().hex[:6]}")

    farm_a = await _create_farm(session_factory, user_a)
    principal_a = _principal(user_a)
    principal_b = _principal(user_b)

    async with session_factory() as session:
        entry = await create_entry(
            session=session,
            principal=principal_a,
            farm_id=farm_a.id,
            data=PlanEntryCreate(
                crop_name="Beans",
                planting_date=date(2027, 4, 1),
                cultivation_mode="rainfed",
                irrigation_mm=None,
                area_ha=1.0,
            ),
        )
        entry_id = entry.id

    # User B attempts to delete using User A's farm_id → NotFoundError
    with pytest.raises(NotFoundError):
        async with session_factory() as session:
            await delete_entry(
                session=session,
                principal=principal_b,
                farm_id=farm_a.id,
                entry_id=entry_id,
            )

    # Entry should still exist
    async with session_factory() as session:
        result = await session.execute(
            select(PlanEntry).where(PlanEntry.id == entry_id)
        )
        assert result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_delete_removes_entry_for_correct_owner(session_factory):
    """DELETE by the owning user actually removes the entry.

    Requirements: 2.6
    """
    user = await _create_user(session_factory, f"del-own-{uuid.uuid4().hex[:6]}")
    farm = await _create_farm(session_factory, user)
    principal = _principal(user)

    async with session_factory() as session:
        entry = await create_entry(
            session=session,
            principal=principal,
            farm_id=farm.id,
            data=PlanEntryCreate(
                crop_name="Carrot",
                planting_date=date(2027, 2, 1),
                cultivation_mode="rainfed",
                irrigation_mm=None,
                area_ha=0.5,
            ),
        )
        entry_id = entry.id

    async with session_factory() as session:
        await delete_entry(
            session=session,
            principal=principal,
            farm_id=farm.id,
            entry_id=entry_id,
        )

    async with session_factory() as session:
        result = await session.execute(
            select(PlanEntry).where(PlanEntry.id == entry_id)
        )
        assert result.scalar_one_or_none() is None, "Entry should be deleted"
