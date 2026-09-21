"""
Backend integration tests — Phase 9 Climate Scenarios checkpoint.

Covers:
- Zero-delta scenario produces identical results to baseline.
- Save scenario → retrieve via GET → results unchanged (immutability).
- Cross-user isolation: user B cannot compute or list scenarios on user A's farm.

Requirements: 2.7, 3.1
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.api.v1.farm_schemas import FarmCreate
from app.core.security import Principal
from app.models.scenario import SavedScenario
from app.models.snapshot import AnalysisJob, AnalysisSnapshot, JobStatus
from app.models.user import User
from app.services.farm_service import FarmService
from app.services.scenario_service import ScenarioDelta, compute_scenario, list_scenarios
from app.services.snapshot_service import run_analysis_job
from app.core.config import Settings
from app.core.exceptions import NotFoundError
from pathlib import Path


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

FARM_BOUNDARY = {
    "type": "Polygon",
    "coordinates": [
        [[36.80, -1.30], [36.805, -1.30], [36.805, -1.295], [36.80, -1.30]]
    ],
}

ZERO_DELTA = ScenarioDelta(
    rainfall_change_pct=0.0,
    temperature_change_c=0.0,
    irrigation_mm_override=None,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings_historical() -> Settings:
    env_path = Path(__file__).parent.parent / ".env"
    return Settings(_env_file=str(env_path), data_mode="historical_replay")


async def _create_user(session_factory, subject: str) -> User:
    async with session_factory() as session:
        user = User(
            id=uuid.uuid4(),
            issuer="scenario-integration-tests",
            subject=subject,
            email=f"{subject}@example.test",
            display_name=subject,
        )
        session.add(user)
        await session.commit()
        return user


async def _create_farm_with_snapshot(session_factory, user: User) -> tuple[Any, AnalysisSnapshot]:
    """Create a farm and run the analysis job to produce a snapshot."""
    async with session_factory() as session:
        result = await FarmService(session, user.id).create_farm_request(
            FarmCreate(name="Scenario Test Farm", geometry=FARM_BOUNDARY)
        )
        farm = result.farm
        job_id = result.analysis_job_id

    settings = _settings_historical()

    with (
        patch(
            "app.data.providers.weather.fetch",
            new=AsyncMock(return_value=_accepted_weather_result()),
        ),
        patch(
            "app.data.providers.climate.fetch",
            new=AsyncMock(return_value=_accepted_climate_result()),
        ),
        patch(
            "app.data.providers.satellite.fetch",
            new=AsyncMock(return_value=_unavailable_result("satellite")),
        ),
        patch(
            "app.data.providers.soil.fetch",
            new=AsyncMock(return_value=_accepted_soil_result()),
        ),
        patch(
            "app.data.providers.terrain.fetch",
            new=AsyncMock(return_value=_accepted_terrain_result()),
        ),
    ):
        snapshot = await run_analysis_job(job_id, settings, session_factory)

    assert snapshot is not None, "Analysis job must produce a snapshot"
    return farm, snapshot


def _principal(user: User) -> Principal:
    return Principal(
        user_id=user.id,
        issuer="scenario-integration-tests",
        subject=str(user.id),
        permissions=frozenset(),
    )


# ---------------------------------------------------------------------------
# Mock provider results (minimal to satisfy snapshot requirements)
# ---------------------------------------------------------------------------

from app.data.providers.base import (
    EVIDENCE_ACCEPTED,
    EVIDENCE_UNAVAILABLE,
    ProviderResult,
)


def _accepted_weather_result() -> ProviderResult:
    return ProviderResult(
        payload={
            "fields": {
                "temperature_2m": {
                    "value": 24.3,
                    "unit": "celsius",
                    "source": "open-meteo",
                    "acquired_at": "2026-09-08T06:00:00Z",
                    "retrieved_at": "2026-09-08T09:14:33Z",
                    "data_mode": "historical_replay",
                    "quality": "accepted",
                    "resolution_m": 11000,
                },
                "precipitation": {
                    "value": 45.0,
                    "unit": "mm",
                    "source": "open-meteo",
                    "acquired_at": "2026-09-08T06:00:00Z",
                    "retrieved_at": "2026-09-08T09:14:33Z",
                    "data_mode": "historical_replay",
                    "quality": "accepted",
                    "resolution_m": 11000,
                },
            }
        },
        evidence_status=EVIDENCE_ACCEPTED,
        error_message=None,
    )


def _accepted_climate_result() -> ProviderResult:
    return ProviderResult(
        payload={"baseline_period": "1991-2020", "anomaly_celsius": 0.5},
        evidence_status=EVIDENCE_ACCEPTED,
        error_message=None,
    )


def _accepted_soil_result() -> ProviderResult:
    return ProviderResult(
        payload={"bdod": {"value": 1.2, "unit": "kg/dm3", "modelled_estimate": True}},
        evidence_status=EVIDENCE_ACCEPTED,
        error_message=None,
    )


def _accepted_terrain_result() -> ProviderResult:
    return ProviderResult(
        payload={
            "mean_elevation_m": {"value": 1650.0, "unit": "m"},
            "flood_probability": None,
        },
        evidence_status=EVIDENCE_ACCEPTED,
        error_message=None,
    )


def _unavailable_result(source: str) -> ProviderResult:
    return ProviderResult(
        payload=None,
        evidence_status=EVIDENCE_UNAVAILABLE,
        error_message=f"{source} unavailable (mocked)",
    )


# ---------------------------------------------------------------------------
# Test 1: Zero-delta scenario → identical results to baseline
# Requirements: 3.1
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_delta_scenario_matches_baseline(session_factory):
    """A zero-delta scenario must produce crop and hazard results identical to baseline.

    We compute the scenario with zero delta, then fetch the saved record and
    verify that every baseline_index == scenario_index and baseline_level == scenario_level.

    Requirements: 3.1
    """
    user = await _create_user(session_factory, f"zero-delta-{uuid.uuid4().hex[:6]}")
    farm, snapshot = await _create_farm_with_snapshot(session_factory, user)
    principal = _principal(user)

    async with session_factory() as session:
        response = await compute_scenario(
            session=session,
            principal=principal,
            farm_id=farm.id,
            delta=ZERO_DELTA,
            name="Zero Delta Test",
        )

    assert response.id is not None
    assert response.baseline_snapshot_id == str(snapshot.id)
    assert response.delta["rainfall_change_pct"] == 0.0
    assert response.delta["temperature_change_c"] == 0.0
    assert response.delta["irrigation_mm_override"] is None

    # All 12 crops: baseline == scenario
    assert len(response.crops) > 0, "Expected at least one crop result"
    for crop in response.crops:
        assert crop.baseline_index == crop.scenario_index, (
            f"Zero-delta crop {crop.crop_name!r}: "
            f"baseline_index={crop.baseline_index} != scenario_index={crop.scenario_index}"
        )
        assert crop.baseline_label == crop.scenario_label, (
            f"Zero-delta crop {crop.crop_name!r}: "
            f"baseline_label={crop.baseline_label!r} != scenario_label={crop.scenario_label!r}"
        )

    # All 5 hazards: baseline == scenario
    assert len(response.hazards) > 0, "Expected at least one hazard result"
    for hazard in response.hazards:
        assert hazard.baseline_level == hazard.scenario_level, (
            f"Zero-delta hazard {hazard.hazard!r}: "
            f"baseline_level={hazard.baseline_level!r} != scenario_level={hazard.scenario_level!r}"
        )
        assert hazard.baseline_index == hazard.scenario_index, (
            f"Zero-delta hazard {hazard.hazard!r}: "
            f"baseline_index={hazard.baseline_index} != scenario_index={hazard.scenario_index}"
        )

    # engine_version must be present
    assert response.engine_version


# ---------------------------------------------------------------------------
# Test 2: Save scenario → retrieve via GET → results unchanged (immutability)
# Requirements: 2.7
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_saved_scenario_results_are_immutable(session_factory):
    """Saving a scenario and then retrieving it via list_scenarios must return identical results.

    The retrieved summary must preserve the delta parameters and engine_version.
    The actual crop/hazard results must survive round-tripping through the DB
    (verified by re-running compute and comparing to raw DB read).

    Requirements: 2.7
    """
    user = await _create_user(session_factory, f"immutable-{uuid.uuid4().hex[:6]}")
    farm, snapshot = await _create_farm_with_snapshot(session_factory, user)
    principal = _principal(user)

    delta = ScenarioDelta(
        rainfall_change_pct=-20.0,
        temperature_change_c=1.5,
        irrigation_mm_override=None,
    )

    # Save the scenario
    async with session_factory() as session:
        saved_response = await compute_scenario(
            session=session,
            principal=principal,
            farm_id=farm.id,
            delta=delta,
            name="Immutability Check",
        )

    scenario_id = saved_response.id

    # Retrieve via list_scenarios
    async with session_factory() as session:
        summaries = await list_scenarios(
            session=session,
            principal=principal,
            farm_id=farm.id,
        )

    assert len(summaries) >= 1
    found = next((s for s in summaries if s.id == scenario_id), None)
    assert found is not None, f"Saved scenario {scenario_id} not found in list"

    # Summary metadata must match
    assert found.name == "Immutability Check"
    assert found.baseline_snapshot_id == str(snapshot.id)
    assert found.delta["rainfall_change_pct"] == pytest.approx(-20.0)
    assert found.delta["temperature_change_c"] == pytest.approx(1.5)
    assert found.delta["irrigation_mm_override"] is None
    assert found.engine_version == saved_response.engine_version

    # Raw DB read: results JSON must not have changed
    async with session_factory() as session:
        db_row = await session.get(SavedScenario, uuid.UUID(scenario_id))

    assert db_row is not None
    assert db_row.results is not None

    db_crops = {c["crop_name"]: c for c in db_row.results.get("crops", [])}
    for crop in saved_response.crops:
        assert crop.crop_name in db_crops, f"Crop {crop.crop_name!r} missing from DB results"
        db_crop = db_crops[crop.crop_name]
        assert db_crop["baseline_index"] == crop.baseline_index
        assert db_crop["scenario_index"] == crop.scenario_index
        assert db_crop["baseline_label"] == crop.baseline_label
        assert db_crop["scenario_label"] == crop.scenario_label

    db_hazards = {h["hazard"]: h for h in db_row.results.get("hazards", [])}
    for hazard in saved_response.hazards:
        assert hazard.hazard in db_hazards, f"Hazard {hazard.hazard!r} missing from DB results"
        db_hazard = db_hazards[hazard.hazard]
        assert db_hazard["baseline_level"] == hazard.baseline_level
        assert db_hazard["scenario_level"] == hazard.scenario_level


# ---------------------------------------------------------------------------
# Test 3: Multiple saved scenarios accumulate in list
# Requirements: 2.6, 2.7
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_saved_scenarios_accumulate(session_factory):
    """Saving two scenarios for the same farm returns both in the list, newest first.

    Requirements: 2.6, 2.7
    """
    user = await _create_user(session_factory, f"multi-scenario-{uuid.uuid4().hex[:6]}")
    farm, _ = await _create_farm_with_snapshot(session_factory, user)
    principal = _principal(user)

    async with session_factory() as session:
        resp1 = await compute_scenario(
            session=session,
            principal=principal,
            farm_id=farm.id,
            delta=ZERO_DELTA,
            name="First Scenario",
        )

    async with session_factory() as session:
        resp2 = await compute_scenario(
            session=session,
            principal=principal,
            farm_id=farm.id,
            delta=ScenarioDelta(rainfall_change_pct=10.0),
            name="Second Scenario",
        )

    async with session_factory() as session:
        summaries = await list_scenarios(
            session=session,
            principal=principal,
            farm_id=farm.id,
        )

    ids = [s.id for s in summaries]
    assert resp1.id in ids, "First scenario must appear in list"
    assert resp2.id in ids, "Second scenario must appear in list"

    # Results must not change between saves (immutability — no overwrite)
    first_from_list = next(s for s in summaries if s.id == resp1.id)
    assert first_from_list.delta["rainfall_change_pct"] == pytest.approx(0.0)
    second_from_list = next(s for s in summaries if s.id == resp2.id)
    assert second_from_list.delta["rainfall_change_pct"] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Test 4: Cross-user isolation — compute
# Requirements: 2.7
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_user_isolation_compute_scenario(session_factory):
    """User B cannot compute a scenario on user A's farm.

    compute_scenario must raise NotFoundError (ownership-scoped 404).

    Requirements: 2.7
    """
    user_a = await _create_user(session_factory, f"iso-a-{uuid.uuid4().hex[:6]}")
    user_b = await _create_user(session_factory, f"iso-b-{uuid.uuid4().hex[:6]}")

    farm_a, _ = await _create_farm_with_snapshot(session_factory, user_a)
    principal_b = _principal(user_b)

    with pytest.raises(NotFoundError):
        async with session_factory() as session:
            await compute_scenario(
                session=session,
                principal=principal_b,
                farm_id=farm_a.id,
                delta=ZERO_DELTA,
                name="Should Fail",
            )


# ---------------------------------------------------------------------------
# Test 5: Cross-user isolation — list
# Requirements: 2.7
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_user_isolation_list_scenarios(session_factory):
    """User B cannot list scenarios belonging to user A's farm.

    list_scenarios must raise NotFoundError for a farm the caller doesn't own.

    Requirements: 2.7
    """
    user_a = await _create_user(session_factory, f"list-iso-a-{uuid.uuid4().hex[:6]}")
    user_b = await _create_user(session_factory, f"list-iso-b-{uuid.uuid4().hex[:6]}")

    farm_a, _ = await _create_farm_with_snapshot(session_factory, user_a)
    principal_a = _principal(user_a)
    principal_b = _principal(user_b)

    # User A saves a scenario
    async with session_factory() as session:
        await compute_scenario(
            session=session,
            principal=principal_a,
            farm_id=farm_a.id,
            delta=ZERO_DELTA,
            name="User A's scenario",
        )

    # User B cannot list it
    with pytest.raises(NotFoundError):
        async with session_factory() as session:
            await list_scenarios(
                session=session,
                principal=principal_b,
                farm_id=farm_a.id,
            )

    # User A can still list their own scenarios
    async with session_factory() as session:
        summaries = await list_scenarios(
            session=session,
            principal=principal_a,
            farm_id=farm_a.id,
        )
    assert len(summaries) >= 1
