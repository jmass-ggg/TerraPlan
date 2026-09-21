"""
API contract tests for the Annual Planner (Phase 8).

Verifies:
- GET /crop-plan returns exactly 12 months (Requirements 1.3)
- POST /entries creates an entry successfully (Requirements 2.1, 2.2)
- POST /entries returns 422 on overlap (Requirements 2.2, 2.3)
- DELETE /entries/{id} removes an entry (Requirements 2.6)
- POST /proposals/{id}/accept updates the entry (Requirements 4.3)

These are contract-level tests: planner_service functions are stubbed so the
tests focus on HTTP semantics, serialisation, and error shapes — not on
database behaviour (which is covered by integration tests).

Requirements: 1.3, 2.2, 2.5, 4.3
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_current_principal, get_request_session
from app.core.exceptions import FarmValidationError, NotFoundError
from app.core.security import Principal
from app.main import create_app
from app.core.config import Settings


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

USER_ID = UUID("10000000-0000-4000-8000-000000000099")
FARM_ID = UUID("20000000-0000-4000-8000-000000000099")
ENTRY_ID = UUID("30000000-0000-4000-8000-000000000099")
PROPOSAL_ID = UUID("40000000-0000-4000-8000-000000000099")
SNAPSHOT_ID = UUID("50000000-0000-4000-8000-000000000099")

_NOW = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
_PLANTING = date(2026, 3, 1)
_HARVEST = date(2026, 7, 1)


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------


def _settings() -> Settings:
    return Settings(
        environment="development",
        data_mode="demonstration",
        auth={"mode": "local_demo"},
        demo={"local_only": True, "isolated_database": True},
        database={"host": "localhost", "name": "test", "user": "test"},
        cors={"origins": []},
    )


def _make_entry(
    entry_id: UUID = ENTRY_ID,
    farm_id: UUID = FARM_ID,
    suitability_index: int = 72,
    planting_date: date = _PLANTING,
    harvest_date: date = _HARVEST,
) -> object:
    """Return a minimal PlanEntry-like object for stubbing."""
    return SimpleNamespace(
        id=entry_id,
        farm_id=farm_id,
        crop_name="Maize",
        planting_date=planting_date,
        harvest_date=harvest_date,
        cultivation_mode="rainfed",
        irrigation_mm=None,
        area_ha=2.5,
        snapshot_id=None,
        data_mode="demonstration",
        suitability_index=suitability_index,
        engine_version="v1.0",
        created_at=_NOW,
        updated_at=None,
    )


def _make_month(month: int) -> dict:
    return {
        "month": month,
        "month_name": "January",
        "data_mode": "demonstration",
        "snapshot_id": None,
        "recommendations": [
            {"crop_name": "Maize", "suitability_index": 72, "label": "Good", "limiting_factor": None}
        ],
    }


def _make_annual_plan_response(entries=None, proposals=None):
    """Stub AnnualPlanResponse with exactly 12 months."""
    from app.services.planner_service import AnnualPlanResponse, MonthRecommendation

    months = [
        MonthRecommendation(
            month=m,
            month_name=f"Month{m}",
            data_mode="demonstration",
            snapshot_id=None,
            recommendations=[
                {"crop_name": "Maize", "suitability_index": 72, "label": "Good", "limiting_factor": None}
            ],
        )
        for m in range(1, 13)
    ]
    return AnnualPlanResponse(
        farm_id=str(FARM_ID),
        year=2026,
        months=months,
        entries=entries or [],
        proposals=proposals or [],
    )


def _make_proposal(
    proposal_id: UUID = PROPOSAL_ID,
    entry_id: UUID = ENTRY_ID,
    farm_id: UUID = FARM_ID,
    old_index: int = 60,
    new_index: int = 75,
) -> object:
    return SimpleNamespace(
        id=proposal_id,
        farm_id=farm_id,
        entry_id=entry_id,
        old_suitability_index=old_index,
        new_suitability_index=new_index,
        changed_inputs={"rainfall": {"old": 150, "new": 180}},
        new_snapshot_id=SNAPSHOT_ID,
        issue_date=_NOW,
        status="pending",
        created_at=_NOW,
    )


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------


def _make_planner_app():
    """Create a test app with minimal overrides (no DB required)."""
    app = create_app(_settings())

    principal = Principal(
        user_id=USER_ID,
        issuer="https://issuer.example",
        subject="planner-contract-subject",
        permissions=frozenset(),
    )

    class _NullSession:
        async def execute(self, *a, **kw):
            return None

        async def commit(self):
            pass

        async def rollback(self):
            pass

        async def close(self):
            pass

    async def principal_override():
        return principal

    async def session_override():
        yield _NullSession()

    app.dependency_overrides[get_current_principal] = principal_override
    app.dependency_overrides[get_request_session] = session_override
    return app


async def _request(app, method: str, path: str, **kwargs):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.request(method, path, **kwargs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_crop_plan_returns_exactly_12_months():
    """GET /crop-plan returns exactly 12 calendar months regardless of saved entries.

    Requirements: 1.3
    """
    app = _make_planner_app()
    annual_response = _make_annual_plan_response()

    with patch(
        "app.services.planner_service.get_annual_plan",
        new=AsyncMock(return_value=annual_response),
    ):
        response = await _request(
            app, "GET", f"/api/v1/farms/{FARM_ID}/crop-plan", params={"year": 2026}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["farm_id"] == str(FARM_ID)
    assert body["year"] == 2026
    assert len(body["months"]) == 12
    months_returned = [m["month"] for m in body["months"]]
    assert months_returned == list(range(1, 13))


@pytest.mark.asyncio
async def test_get_crop_plan_includes_entries_and_proposals():
    """GET /crop-plan includes saved entries and pending proposals.

    Requirements: 2.4
    """
    app = _make_planner_app()
    entry = _make_entry()
    proposal = _make_proposal()
    annual_response = _make_annual_plan_response(entries=[entry], proposals=[proposal])

    with patch(
        "app.services.planner_service.get_annual_plan",
        new=AsyncMock(return_value=annual_response),
    ):
        response = await _request(
            app, "GET", f"/api/v1/farms/{FARM_ID}/crop-plan", params={"year": 2026}
        )

    assert response.status_code == 200
    body = response.json()
    assert len(body["entries"]) == 1
    assert body["entries"][0]["id"] == str(ENTRY_ID)
    assert body["entries"][0]["crop_name"] == "Maize"
    assert len(body["proposals"]) == 1
    assert body["proposals"][0]["id"] == str(PROPOSAL_ID)


@pytest.mark.asyncio
async def test_create_entry_success():
    """POST /entries creates an entry and returns 201 with the entry body.

    Requirements: 2.1
    """
    app = _make_planner_app()
    entry = _make_entry()

    with patch(
        "app.services.planner_service.create_entry",
        new=AsyncMock(return_value=entry),
    ):
        response = await _request(
            app,
            "POST",
            f"/api/v1/farms/{FARM_ID}/crop-plan/entries",
            json={
                "crop_name": "Maize",
                "planting_date": "2026-03-01",
                "cultivation_mode": "rain_fed",
                "area_ha": 2.5,
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == str(ENTRY_ID)
    assert body["crop_name"] == "Maize"
    assert body["planting_date"] == "2026-03-01"
    assert body["harvest_date"] == "2026-07-01"
    assert body["suitability_index"] == 72
    assert body["data_mode"] == "demonstration"
    assert body["engine_version"] == "v1.0"


@pytest.mark.asyncio
async def test_create_entry_returns_422_on_overlap():
    """POST /entries returns 422 with OVERLAP_CONFLICT when the window conflicts.

    Requirements: 2.2, 2.3
    """
    app = _make_planner_app()

    with patch(
        "app.services.planner_service.create_entry",
        new=AsyncMock(
            side_effect=FarmValidationError(
                field="body.planting_date",
                detail_code="OVERLAP_CONFLICT",
                message=f"Planting window overlaps with existing entry {ENTRY_ID}",
            )
        ),
    ):
        response = await _request(
            app,
            "POST",
            f"/api/v1/farms/{FARM_ID}/crop-plan/entries",
            json={
                "crop_name": "Maize",
                "planting_date": "2026-04-01",
                "cultivation_mode": "rain_fed",
                "area_ha": 1.0,
            },
        )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    detail = error["details"][0]
    assert detail["code"] == "OVERLAP_CONFLICT"
    assert detail["field"] == "body.planting_date"
    assert str(ENTRY_ID) in detail["message"]


@pytest.mark.asyncio
async def test_delete_entry_removes_entry():
    """DELETE /entries/{id} returns 204 with empty body.

    Requirements: 2.6
    """
    app = _make_planner_app()

    with patch(
        "app.services.planner_service.delete_entry",
        new=AsyncMock(return_value=None),
    ):
        response = await _request(
            app,
            "DELETE",
            f"/api/v1/farms/{FARM_ID}/crop-plan/entries/{ENTRY_ID}",
        )

    assert response.status_code == 204
    assert response.content == b""


@pytest.mark.asyncio
async def test_delete_entry_not_found_returns_404():
    """DELETE /entries/{id} for an unknown entry returns 404.

    Requirements: 2.6
    """
    app = _make_planner_app()
    missing_id = uuid.uuid4()

    with patch(
        "app.services.planner_service.delete_entry",
        new=AsyncMock(side_effect=NotFoundError(f"Plan entry {missing_id} not found")),
    ):
        response = await _request(
            app,
            "DELETE",
            f"/api/v1/farms/{FARM_ID}/crop-plan/entries/{missing_id}",
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_proposal_accept_updates_entry():
    """POST /proposals/{id}/accept returns 200 with the updated entry.

    Requirements: 4.3
    """
    app = _make_planner_app()
    # Entry after proposal is accepted has the new suitability_index
    updated_entry = _make_entry(suitability_index=75)

    with patch(
        "app.services.planner_service.accept_proposal",
        new=AsyncMock(return_value=updated_entry),
    ):
        response = await _request(
            app,
            "POST",
            f"/api/v1/farms/{FARM_ID}/crop-plan/proposals/{PROPOSAL_ID}/accept",
        )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(ENTRY_ID)
    assert body["suitability_index"] == 75


@pytest.mark.asyncio
async def test_proposal_accept_not_found_returns_404():
    """POST /proposals/{id}/accept for unknown/already-accepted proposal returns 404.

    Requirements: 4.3
    """
    app = _make_planner_app()

    with patch(
        "app.services.planner_service.accept_proposal",
        new=AsyncMock(
            side_effect=NotFoundError(f"Change proposal {PROPOSAL_ID} not found or not pending")
        ),
    ):
        response = await _request(
            app,
            "POST",
            f"/api/v1/farms/{FARM_ID}/crop-plan/proposals/{PROPOSAL_ID}/accept",
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_create_entry_missing_irrigation_mm_returns_422():
    """POST /entries with cultivation_mode=irrigated but no irrigation_mm returns 422.

    Requirements: 2.1
    """
    app = _make_planner_app()

    with patch("app.services.planner_service.create_entry", new=AsyncMock()):
        response = await _request(
            app,
            "POST",
            f"/api/v1/farms/{FARM_ID}/crop-plan/entries",
            json={
                "crop_name": "Maize",
                "planting_date": "2026-03-01",
                "cultivation_mode": "irrigated",
                "area_ha": 1.0,
                # irrigation_mm deliberately omitted
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
