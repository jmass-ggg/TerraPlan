"""Phase 4 farm persistence properties and integration evidence."""

import asyncio
import uuid

import pytest
from geoalchemy2.shape import to_shape
from sqlalchemy import func, select

from app.api.v1.farm_schemas import FarmCreate, FarmUpdate
from app.core.exceptions import (
    IdempotencyConflict,
    StaleRevisionError,
)
from app.models.farm import Farm, FarmGeometryRevision
from app.models.user import User
from app.repositories.base import NotFoundError
from app.services.farm_service import FarmService


BOUNDARY = {
    "type": "Polygon",
    "coordinates": [[[36.80, -1.30], [36.805, -1.30], [36.805, -1.295], [36.80, -1.30]]],
}
BOUNDARY_2 = {
    "type": "Polygon",
    "coordinates": [[[36.80, -1.30], [36.806, -1.30], [36.806, -1.294], [36.80, -1.30]]],
}
BOUNDARY_3 = {
    "type": "Polygon",
    "coordinates": [[[36.80, -1.30], [36.807, -1.30], [36.807, -1.293], [36.80, -1.30]]],
}


async def _user(session_factory, subject: str) -> User:
    async with session_factory() as session:
        user = User(
            id=uuid.uuid4(),
            issuer="phase-4-tests",
            subject=subject,
            email=f"{subject}@example.test",
            display_name=subject,
        )
        session.add(user)
        await session.commit()
        return user


@pytest.mark.asyncio
async def test_property_1_persistence_round_trip_after_new_session(session_factory):
    user = await _user(session_factory, "round-trip")
    async with session_factory() as session:
        result = await FarmService(session, user.id).create_farm_request(
            FarmCreate(name="Persistent Farm", geometry=BOUNDARY)
        )
        farm_id = result.farm.id
        expected_area = result.farm.current_geometry.hectares

    async with session_factory() as restarted_session:
        farm = await FarmService(restarted_session, user.id).get_farm(farm_id)
        assert farm.name == "Persistent Farm"
        assert farm.current_geometry_revision == 1
        assert farm.current_geometry.hectares == pytest.approx(expected_area)
        assert to_shape(farm.current_geometry.geometry).__geo_interface__["coordinates"]


@pytest.mark.asyncio
async def test_property_8_idempotent_creation_and_payload_conflict(session_factory):
    user = await _user(session_factory, "idempotency")
    key = uuid.uuid4()
    async with session_factory() as session:
        service = FarmService(session, user.id)
        first = await service.create_farm_request(
            FarmCreate(name="Same Farm", geometry=BOUNDARY), key
        )
        second = await service.create_farm_request(
            FarmCreate(name="Same Farm", geometry=BOUNDARY), key
        )
        assert first.created is True
        assert second.created is False
        assert first.farm.id == second.farm.id

        with pytest.raises(IdempotencyConflict):
            await service.create_farm_request(
                FarmCreate(name="Different Farm", geometry=BOUNDARY), key
            )

    async with session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(Farm))
        revision_count = await session.scalar(
            select(func.count()).select_from(FarmGeometryRevision)
        )
        assert count == revision_count == 1


@pytest.mark.asyncio
async def test_property_6_revision_monotonicity_and_name_only_update(session_factory):
    user = await _user(session_factory, "revisions")
    async with session_factory() as session:
        service = FarmService(session, user.id)
        created = await service.create_farm_request(
            FarmCreate(name="Revision Farm", geometry=BOUNDARY)
        )
        farm_id = created.farm.id
        for expected, geometry in enumerate((BOUNDARY_2, BOUNDARY_3), start=1):
            farm = await service.update_farm_request(
                farm_id,
                FarmUpdate(geometry=geometry, expected_revision=expected),
            )
            assert farm.current_geometry_revision == expected + 1
            assert farm.current_geometry.revision == expected + 1

        renamed = await service.update_farm_request(
            farm_id, FarmUpdate(name="Renamed Farm")
        )
        assert renamed.name == "Renamed Farm"
        assert renamed.current_geometry_revision == 3


@pytest.mark.asyncio
async def test_property_7_stale_write_does_not_change_geometry(session_factory):
    user = await _user(session_factory, "stale")
    async with session_factory() as session:
        service = FarmService(session, user.id)
        created = await service.create_farm_request(
            FarmCreate(name="Stale Farm", geometry=BOUNDARY)
        )
        farm_id = created.farm.id
        with pytest.raises(StaleRevisionError):
            await service.update_farm_request(
                farm_id,
                FarmUpdate(geometry=BOUNDARY_2, expected_revision=2),
            )

    async with session_factory() as session:
        farm = await FarmService(session, user.id).get_farm(farm_id)
        assert farm.current_geometry_revision == 1
        count = await session.scalar(
            select(func.count()).select_from(FarmGeometryRevision)
        )
        assert count == 1


@pytest.mark.asyncio
async def test_property_2_all_writes_are_owner_isolated(session_factory):
    owner = await _user(session_factory, "owner")
    outsider = await _user(session_factory, "outsider")
    async with session_factory() as session:
        created = await FarmService(session, owner.id).create_farm_request(
            FarmCreate(name="Private Farm", geometry=BOUNDARY)
        )
        farm_id = created.farm.id

    async with session_factory() as session:
        service = FarmService(session, outsider.id)
        with pytest.raises(NotFoundError):
            await service.update_farm_request(farm_id, FarmUpdate(name="Stolen"))
        with pytest.raises(NotFoundError):
            await service.update_farm_request(
                farm_id,
                FarmUpdate(geometry=BOUNDARY_2, expected_revision=1),
            )
        with pytest.raises(NotFoundError):
            await service.delete_farm(farm_id)


@pytest.mark.asyncio
async def test_concurrent_patch_allows_exactly_one_revision(session_factory):
    user = await _user(session_factory, "concurrent")
    async with session_factory() as session:
        created = await FarmService(session, user.id).create_farm_request(
            FarmCreate(name="Concurrent Farm", geometry=BOUNDARY)
        )
        farm_id = created.farm.id

    async def update_one(geometry):
        async with session_factory() as session:
            try:
                await FarmService(session, user.id).update_farm_request(
                    farm_id,
                    FarmUpdate(geometry=geometry, expected_revision=1),
                )
                return "ok"
            except StaleRevisionError:
                return "stale"

    outcomes = await asyncio.gather(update_one(BOUNDARY_2), update_one(BOUNDARY_3))
    assert sorted(outcomes) == ["ok", "stale"]


@pytest.mark.asyncio
async def test_delete_owned_farm_no_dependents(session_factory):
    """DELETE owned farm with no dependents → 204, farm absent from DB."""
    user = await _user(session_factory, "delete-bare")
    async with session_factory() as session:
        created = await FarmService(session, user.id).create_farm_request(
            FarmCreate(name="Delete Bare Farm", geometry=BOUNDARY)
        )
        farm_id = created.farm.id

    # Delete succeeds
    async with session_factory() as session:
        await FarmService(session, user.id).delete_farm(farm_id)

    # Farm is gone
    async with session_factory() as session:
        with pytest.raises(NotFoundError):
            await FarmService(session, user.id).get_farm(farm_id)

    # No geometry revisions orphaned
    async with session_factory() as session:
        count = await session.scalar(
            select(func.count()).select_from(FarmGeometryRevision)
            .where(FarmGeometryRevision.farm_id == farm_id)
        )
        assert count == 0


@pytest.mark.asyncio
async def test_delete_farm_with_dependent_records(session_factory):
    """DELETE farm that has analysis_jobs and analysis_snapshots.

    Verifies deletion order:
      1. snapshots (RESTRICT FK on farm_id would block farm delete)
      2. jobs (RESTRICT FK on job_id in snapshots already gone)
      3. farm (cascades geometry_revisions, action_completions, plan_entries)
    No orphan records remain after deletion.
    """
    from app.models.snapshot import AnalysisJob, AnalysisSnapshot, JobStatus
    from app.models.actions import ActionCompletion
    import datetime

    user = await _user(session_factory, "delete-dependents")
    async with session_factory() as session:
        created = await FarmService(session, user.id).create_farm_request(
            FarmCreate(name="Delete Deps Farm", geometry=BOUNDARY)
        )
        farm_id = created.farm.id

    # Insert analysis job + snapshot directly
    async with session_factory() as session:
        job_id = uuid.uuid4()
        snap_id = uuid.uuid4()
        session.add(AnalysisJob(
            id=job_id, farm_id=farm_id, geometry_revision=1,
            status=JobStatus.COMPLETED, stages={},
        ))
        await session.flush()
        session.add(AnalysisSnapshot(
            id=snap_id, farm_id=farm_id, geometry_revision=1,
            job_id=job_id,
            valid_time_utc=datetime.datetime.now(datetime.timezone.utc),
            data_mode="demonstration",
            evidence_statuses={"weather": "accepted"},
        ))
        session.add(ActionCompletion(
            id=uuid.uuid4(), farm_id=farm_id,
            action_id="drought-test",
            completed_at=datetime.datetime.now(datetime.timezone.utc),
        ))
        await session.commit()

    # Delete must succeed (not raise)
    async with session_factory() as session:
        await FarmService(session, user.id).delete_farm(farm_id)

    # Farm gone
    async with session_factory() as session:
        farm_exists = await session.scalar(
            select(func.count()).select_from(Farm).where(Farm.id == farm_id)
        )
        assert farm_exists == 0

    # No orphan jobs, snapshots, or geometry revisions
    async with session_factory() as session:
        assert await session.scalar(
            select(func.count()).select_from(AnalysisJob).where(AnalysisJob.farm_id == farm_id)
        ) == 0
        assert await session.scalar(
            select(func.count()).select_from(AnalysisSnapshot).where(AnalysisSnapshot.farm_id == farm_id)
        ) == 0
        assert await session.scalar(
            select(func.count()).select_from(FarmGeometryRevision)
            .where(FarmGeometryRevision.farm_id == farm_id)
        ) == 0


@pytest.mark.asyncio
async def test_delete_nonexistent_farm_raises_not_found(session_factory):
    """DELETE random UUID → NotFoundError (→ 404 at API layer)."""
    user = await _user(session_factory, "delete-404")
    async with session_factory() as session:
        with pytest.raises(NotFoundError):
            await FarmService(session, user.id).delete_farm(uuid.uuid4())


@pytest.mark.asyncio
async def test_delete_another_users_farm_raises_not_found(session_factory):
    """Deleting another user's farm raises NotFoundError (not ForbiddenError).

    The project convention is to return 404 for both absent and inaccessible
    resources to prevent cross-user existence disclosure.
    """
    owner = await _user(session_factory, "delete-owner")
    attacker = await _user(session_factory, "delete-attacker")
    async with session_factory() as session:
        created = await FarmService(session, owner.id).create_farm_request(
            FarmCreate(name="Owner Farm", geometry=BOUNDARY)
        )
        farm_id = created.farm.id

    # Attacker cannot delete owner's farm
    async with session_factory() as session:
        with pytest.raises(NotFoundError):
            await FarmService(session, attacker.id).delete_farm(farm_id)

    # Farm still exists
    async with session_factory() as session:
        farm = await FarmService(session, owner.id).get_farm(farm_id)
        assert farm.id == farm_id


@pytest.mark.asyncio
async def test_delete_farm_absent_from_list(session_factory):
    """After deletion the farm must not appear in the owner's list."""
    user = await _user(session_factory, "delete-list")
    async with session_factory() as session:
        svc = FarmService(session, user.id)
        a = await svc.create_farm_request(FarmCreate(name="Farm A", geometry=BOUNDARY))
        b = await svc.create_farm_request(FarmCreate(name="Farm B", geometry=BOUNDARY_2))

    async with session_factory() as session:
        await FarmService(session, user.id).delete_farm(a.farm.id)

    async with session_factory() as session:
        farms, total = await FarmService(session, user.id).list_farms()
        ids = {f.id for f in farms}
        assert a.farm.id not in ids
        assert b.farm.id in ids
        assert total == 1
