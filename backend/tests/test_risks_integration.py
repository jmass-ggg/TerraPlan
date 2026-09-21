"""
Backend integration tests — Phase 7 Risk Center checkpoint.

Covers:
- All 5 hazards assessed under demonstration context: valid levels returned.
- Action conflict: drought High + heavy_rainfall High → drought irrigation absent.
- PATCH action completion → GET /risks shows completed=true on that action.
- Cross-user: GET /risks on another user's farm returns 404.

Requirements: 1.1, 1.2, 2.2, 7.1, 7.3, 7.4, 8.1
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from geoalchemy2.shape import from_shape
from httpx import ASGITransport, AsyncClient
from shapely.geometry import Point, Polygon
from sqlalchemy import select

from app.api.dependencies import (
    get_app_session_factory,
    get_current_principal,
    get_request_session,
)
from app.api.v1.farm_schemas import FarmCreate
from app.core.config import Settings
from app.core.security import Principal
from app.domain.risk_engine import (
    DROUGHT_HIGH_RATIO,
    HEAVY_RAIN_HIGH_MM,
    LEVEL_HIGH,
    LEVEL_LOW,
    LEVEL_MEDIUM,
    LEVEL_UNKNOWN,
)
from app.domain.snapshot_context import SnapshotContext
from app.main import create_app
from app.models.actions import ActionCompletion
from app.models.user import User
from app.services.farm_service import FarmService


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FARM_BOUNDARY = {
    "type": "Polygon",
    "coordinates": [
        [[36.80, -1.30], [36.805, -1.30], [36.805, -1.295], [36.80, -1.30]]
    ],
}

VALID_LEVELS = {LEVEL_LOW, LEVEL_MEDIUM, LEVEL_HIGH, LEVEL_UNKNOWN}
EXPECTED_HAZARDS = {"drought", "heat", "heavy_rainfall", "flood_exposure", "wind"}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _create_user(session_factory, subject: str) -> User:
    async with session_factory() as session:
        user = User(
            id=uuid.uuid4(),
            issuer="risk-integration-tests",
            subject=subject,
            email=f"{subject}@example.test",
            display_name=subject,
        )
        session.add(user)
        await session.commit()
        return user


async def _create_farm(session_factory, user: User) -> Any:
    async with session_factory() as session:
        service = FarmService(session, user.id)
        result = await service.create_farm_request(
            FarmCreate(name="Risk Test Farm", geometry=FARM_BOUNDARY)
        )
        return result


def _settings() -> Settings:
    return Settings(
        environment="development",
        data_mode="demonstration",
        auth={"mode": "local_demo"},
        demo={"local_only": True, "isolated_database": True},
        database={"host": "localhost", "name": "test", "user": "test"},
        cors={"origins": []},
    )


def _make_app(user: User, session_factory):
    """Build a test app wired to the real test session factory and a stub principal."""
    app = create_app(_settings())
    principal = Principal(
        user_id=user.id,
        issuer="risk-integration-tests",
        subject=str(user.id),
        permissions=frozenset(),
    )

    async def principal_override():
        return principal

    async def session_factory_override():
        return session_factory

    async def session_override():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_current_principal] = principal_override
    app.dependency_overrides[get_app_session_factory] = session_factory_override
    app.dependency_overrides[get_request_session] = session_override
    return app


async def _get(app, path: str):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path)


async def _patch(app, path: str):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.patch(path)


# ---------------------------------------------------------------------------
# Test 1: All 5 hazards assessed under demonstration context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_five_hazards_assessed_under_demonstration_context(session_factory):
    """GET /risks with no snapshot falls back to demonstration profile.

    All 5 hazards must be present, each with a valid level.

    Requirements: 1.1, 1.2, 2.2, 8.1
    """
    user = await _create_user(session_factory, "demo-context-risks")
    result = await _create_farm(session_factory, user)
    farm_id = result.farm.id

    app = _make_app(user, session_factory)
    response = await _get(app, f"/api/v1/farms/{farm_id}/risks")

    assert response.status_code == 200, response.text
    body = response.json()

    # Top-level response fields
    assert body["farm_id"] == str(farm_id)
    assert body["data_mode"] == "demonstration"
    assert body["snapshot_id"] is None, "Demonstration fallback must have null snapshot_id"
    assert body["engine_version"] == "farmtwin-risk-v1"

    assessments = body["assessments"]
    assert len(assessments) == 5, f"Expected 5 assessments, got {len(assessments)}"

    hazard_names = {a["hazard"] for a in assessments}
    assert hazard_names == EXPECTED_HAZARDS, f"Unexpected hazards: {hazard_names}"

    for assessment in assessments:
        assert assessment["level"] in VALID_LEVELS, (
            f"Hazard {assessment['hazard']!r} has invalid level {assessment['level']!r}"
        )
        assert assessment["engine_version"] == "farmtwin-risk-v1"
        assert assessment["data_mode"] == "demonstration"
        assert assessment["snapshot_id"] is None
        assert 0 <= assessment["index"] <= 100
        assert assessment["driver"]
        assert assessment["explanation"]

    # Standard response headers
    assert response.headers.get("X-Request-ID")
    assert response.headers.get("X-FarmTwin-Data-Mode") == "demonstration"


# ---------------------------------------------------------------------------
# Test 2: Action conflict — drought High + heavy_rainfall High
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_action_conflict_drought_irrigation_absent_when_heavy_rain_high(
    session_factory,
):
    """Drought High + Heavy_Rainfall High → drought irrigation action absent.

    The drought-irrigate-or-mulch rule conflicts with heavy-rain-field-drainage.
    When both hazards are Medium/High simultaneously, the lower-priority drought
    irrigation action must be removed from the resolved action list.

    Requirements: 7.1, 7.3
    """
    user = await _create_user(session_factory, "conflict-resolution")
    result = await _create_farm(session_factory, user)
    farm_id = result.farm.id

    # Build a context where drought is High and heavy_rainfall is High
    # Drought High: ratio < 0.60  →  rainfall = 50, baseline = 200  (ratio = 0.25)
    # Heavy rain High: rain_7d > 120 → 150 mm
    conflict_context = SnapshotContext(
        source="demonstration",
        snapshot_id=None,
        data_mode="demonstration",
        temperature_mean_c=25.0,
        temperature_source="test",
        rainfall_total_mm=50.0,
        rainfall_source="test",
        soil_ph=None,
        soil_clay_pct=None,
        soil_sand_pct=None,
        soil_source=None,
        soil_is_modelled=False,
        ndvi_mean=None,
        ndvi_source=None,
        vpd_kpa=None,
        real_input_fields=frozenset({"rainfall_total_mm", "temperature_mean_c"}),
        demonstration_input_fields=frozenset(),
        rain_7d_mm=150.0,       # Heavy_Rainfall High (>120 mm)
        wind_max_ms=5.0,
        slope_pct=3.0,
        climate_baseline_rainfall_mm=200.0,  # drought ratio = 50/200 = 0.25 → High
    )

    app = _make_app(user, session_factory)

    with patch(
        "app.services.risk_service.context_from_demonstration",
        return_value=conflict_context,
    ):
        response = await _get(app, f"/api/v1/farms/{farm_id}/risks")

    assert response.status_code == 200, response.text
    body = response.json()

    assessments_by_hazard = {a["hazard"]: a for a in body["assessments"]}

    drought = assessments_by_hazard["drought"]
    heavy_rainfall = assessments_by_hazard["heavy_rainfall"]

    assert drought["level"] == LEVEL_HIGH, (
        f"Expected drought High, got {drought['level']!r}"
    )
    assert heavy_rainfall["level"] == LEVEL_HIGH, (
        f"Expected heavy_rainfall High, got {heavy_rainfall['level']!r}"
    )

    # The drought irrigation action must NOT appear in drought actions
    drought_action_ids = {a["id"] for a in drought["actions"]}
    assert "drought-irrigate-or-mulch" not in drought_action_ids, (
        "drought-irrigate-or-mulch must be absent when heavy_rainfall is High "
        f"(conflict resolution failed). Drought actions: {drought_action_ids}"
    )

    # The heavy rain drainage action MUST still be present
    heavy_rain_action_ids = {a["id"] for a in heavy_rainfall["actions"]}
    assert "heavy-rain-field-drainage" in heavy_rain_action_ids, (
        "heavy-rain-field-drainage must be present when heavy_rainfall is High. "
        f"Got: {heavy_rain_action_ids}"
    )


# ---------------------------------------------------------------------------
# Test 3: PATCH action completion → GET /risks shows completed=true
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_action_completion_persists_and_visible_on_get(session_factory):
    """PATCH action completion → GET /risks shows completed=true on that action.

    Also verifies idempotency: second PATCH returns 200 and retains original
    timestamp.

    Requirements: 7.4, 7.5, 8.1
    """
    user = await _create_user(session_factory, "action-completion")
    result = await _create_farm(session_factory, user)
    farm_id = result.farm.id

    action_id = "drought-shift-planting"

    # Use a context where drought is High so the action appears in the response
    drought_high_context = SnapshotContext(
        source="demonstration",
        snapshot_id=None,
        data_mode="demonstration",
        temperature_mean_c=25.0,
        temperature_source="test",
        rainfall_total_mm=50.0,
        rainfall_source="test",
        soil_ph=None,
        soil_clay_pct=None,
        soil_sand_pct=None,
        soil_source=None,
        soil_is_modelled=False,
        ndvi_mean=None,
        ndvi_source=None,
        vpd_kpa=None,
        real_input_fields=frozenset({"rainfall_total_mm", "temperature_mean_c"}),
        demonstration_input_fields=frozenset(),
        rain_7d_mm=10.0,
        wind_max_ms=5.0,
        slope_pct=3.0,
        climate_baseline_rainfall_mm=200.0,  # drought ratio = 0.25 → High
    )

    app = _make_app(user, session_factory)

    with patch(
        "app.services.risk_service.context_from_demonstration",
        return_value=drought_high_context,
    ):
        # 1. PATCH — mark action complete
        patch_response = await _patch(
            app, f"/api/v1/farms/{farm_id}/actions/{action_id}"
        )
        assert patch_response.status_code == 200, patch_response.text
        patch_body = patch_response.json()
        assert patch_body["completed"] is True
        assert patch_body["action_id"] == action_id
        assert patch_body["farm_id"] == str(farm_id)
        assert patch_body["completed_at"] is not None
        first_completed_at = patch_body["completed_at"]

        # 2. Second PATCH — idempotent: must return 200 with same timestamp
        patch_response_2 = await _patch(
            app, f"/api/v1/farms/{farm_id}/actions/{action_id}"
        )
        assert patch_response_2.status_code == 200, patch_response_2.text
        assert patch_response_2.json()["completed_at"] == first_completed_at, (
            "Idempotent PATCH must retain the original completion timestamp"
        )

        # 3. GET /risks — the completed action must show completed=True
        get_response = await _get(app, f"/api/v1/farms/{farm_id}/risks")
        assert get_response.status_code == 200, get_response.text
        risks_body = get_response.json()

    assessments_by_hazard = {a["hazard"]: a for a in risks_body["assessments"]}
    drought_actions = assessments_by_hazard["drought"]["actions"]

    completed_action = next(
        (a for a in drought_actions if a["id"] == action_id), None
    )
    assert completed_action is not None, (
        f"Action {action_id!r} not found in drought actions: "
        f"{[a['id'] for a in drought_actions]}"
    )
    assert completed_action["completed"] is True, (
        f"Expected completed=True for {action_id!r}, got {completed_action['completed']!r}"
    )
    assert completed_action["completed_at"] is not None


# ---------------------------------------------------------------------------
# Test 4: Cross-user isolation — GET /risks on another user's farm returns 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_risks_cross_user_returns_404(session_factory):
    """GET /risks for a farm owned by another user must return 404.

    Requirements: 8.1 (ownership-scoped 404, same as non-existent farm)
    """
    owner = await _create_user(session_factory, "risks-owner")
    intruder = await _create_user(session_factory, "risks-intruder")

    result = await _create_farm(session_factory, owner)
    owner_farm_id = result.farm.id

    # Make the app authenticate as the intruder
    intruder_app = _make_app(intruder, session_factory)
    response = await _get(intruder_app, f"/api/v1/farms/{owner_farm_id}/risks")

    assert response.status_code == 404, (
        f"Expected 404 for cross-user farm access, got {response.status_code}"
    )
    error = response.json()["error"]
    assert error["code"] == "RESOURCE_NOT_FOUND"
    assert error["message"] == "Resource not found"
    assert error["details"] == []
