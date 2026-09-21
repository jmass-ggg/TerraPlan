"""Mandatory API and OpenAPI contract tests through Phase 5.

Feature: backend-foundation, environmental-twin
Property 11: Versioned API contract
Requirements: 7.1-7.7, 8.1-8.6, 14.3, 10.1, 10.2, 10.3
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from geoalchemy2.shape import from_shape
from httpx import ASGITransport, AsyncClient
from shapely.geometry import Point, Polygon

from app.api.dependencies import (
    get_app_session_factory,
    get_current_principal,
    get_farm_service,
    get_request_session,
)
from app.core.config import Settings
from app.core.exceptions import (
    IdempotencyConflict,
    NotFoundError,
    StaleRevisionError,
)
from app.models.snapshot import AnalysisJob, AnalysisSnapshot, JobStatus
from app.services.farm_service import FarmCreationResult
from app.core.security import Principal
from app.main import create_app


USER_ID = UUID("10000000-0000-4000-8000-000000000001")
OWNED_FARM_ID = UUID("20000000-0000-4000-8000-000000000001")
INVISIBLE_FARM_ID = UUID("20000000-0000-4000-8000-000000000002")
ABSENT_FARM_ID = UUID("20000000-0000-4000-8000-000000000003")


def _settings(*, oidc: bool = False) -> Settings:
    auth = (
        {
            "mode": "oidc",
            "issuer": "https://issuer.example",
            "audience": "farmtwin-api",
            "jwks_url": "https://issuer.example/jwks.json",
            "algorithms": ["RS256"],
        }
        if oidc
        else {"mode": "local_demo"}
    )
    return Settings(
        environment="development",
        data_mode="demonstration",
        auth=auth,
        demo={"local_only": not oidc, "isolated_database": not oidc},
        database={"host": "localhost", "name": "test", "user": "test"},
        cors={"origins": []},
    )


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _ProfileSession:
    def __init__(self, user):
        self.user = user

    async def execute(self, statement):
        return _Result(self.user)


class _FarmService:
    def __init__(self, farms):
        self.farms = farms
        self.list_call = None
        self.create_call = None
        self.update_call = None
        self.delete_call = None
        self.idempotency_match = False
        self.write_error = None

    async def list_farms(self, *, limit, offset, sort_by):
        self.list_call = (limit, offset, sort_by)
        return self.farms[offset : offset + limit], len(self.farms)

    async def get_farm(self, farm_id):
        if farm_id != OWNED_FARM_ID:
            raise NotFoundError("not visible")
        return self.farms[0]

    async def create_farm_request(self, data, idempotency_key):
        if self.write_error:
            raise self.write_error
        self.create_call = (data, idempotency_key)
        return FarmCreationResult(self.farms[0], not self.idempotency_match)

    async def update_farm_request(self, farm_id, data):
        if self.write_error:
            raise self.write_error
        self.update_call = (farm_id, data)
        return self.farms[0]

    async def delete_farm(self, farm_id):
        if self.write_error:
            raise self.write_error
        self.delete_call = farm_id


def _farm(farm_id: UUID, name: str, created_at: datetime):
    polygon = from_shape(
        Polygon([(36.8, -1.3), (36.81, -1.3), (36.81, -1.29), (36.8, -1.3)]),
        srid=4326,
    )
    point = from_shape(Point(36.805, -1.295), srid=4326)
    geometry = SimpleNamespace(
        id=uuid4(),
        revision=1,
        geometry=polygon,
        centroid=point,
        label_point=point,
        hectares=1.25,
        created_at=created_at,
    )
    return SimpleNamespace(
        id=farm_id,
        name=name,
        current_geometry_revision=1,
        current_geometry=geometry,
        created_at=created_at,
        updated_at=None,
    )


@pytest.fixture
def contract_app():
    app = create_app(_settings())
    principal = Principal(
        user_id=USER_ID,
        issuer="https://issuer.example",
        subject="subject-1",
        permissions=frozenset(),
    )
    offset = timezone(timedelta(hours=5, minutes=45))
    created = datetime(2025, 6, 1, 12, 0, 0, 123456, tzinfo=offset)
    user = SimpleNamespace(
        id=USER_ID,
        issuer=principal.issuer,
        subject=principal.subject,
        email=None,
        display_name=None,
        preferences={},
        created_at=created,
        updated_at=None,
    )
    farms = [
        _farm(OWNED_FARM_ID, "Alpha", created),
        _farm(UUID("20000000-0000-4000-8000-000000000004"), "Beta", created),
        _farm(UUID("20000000-0000-4000-8000-000000000005"), "Gamma", created),
    ]
    service = _FarmService(farms)

    async def principal_override():
        return principal

    async def session_override():
        yield _ProfileSession(user)

    async def service_override():
        return service

    app.dependency_overrides[get_current_principal] = principal_override
    app.dependency_overrides[get_request_session] = session_override
    app.dependency_overrides[get_farm_service] = service_override
    app.state.contract_service = service
    return app


async def _request(app, method: str, path: str, **kwargs):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.request(method, path, **kwargs)


def test_openapi_documents_phase_5_operations_and_common_errors(contract_app):
    schema = contract_app.openapi()
    expected_paths = {
        "/health",
        "/ready",
        "/api/v1/me",
        "/api/v1/farms",
        "/api/v1/farms/{farm_id}",
        "/api/v1/farms/{farm_id}/decision-support",
        # Phase 2 Conduit endpoints
        "/api/v1/conduit/current",
        "/api/v1/conduit/features",
        "/api/v1/conduit/history",
        "/api/v1/data-sources",
        # Phase 5 Digital Twin endpoints
        "/api/v1/farms/{farm_id}/analysis-jobs",
        "/api/v1/farms/{farm_id}/digital-twin",
        "/api/v1/jobs/{job_id}",
        # Phase 6 Crop Simulator endpoints
        "/api/v1/crops",
        "/api/v1/farms/{farm_id}/simulate-crop",
        # Phase 7 Risk Center endpoints
        "/api/v1/farms/{farm_id}/risks",
        "/api/v1/farms/{farm_id}/actions/{action_id}",
        # Phase 8 Annual Planner endpoints
        "/api/v1/farms/{farm_id}/crop-plan",
        "/api/v1/farms/{farm_id}/crop-plan/entries",
        "/api/v1/farms/{farm_id}/crop-plan/entries/{entry_id}",
        "/api/v1/farms/{farm_id}/crop-plan/proposals/{proposal_id}/accept",
        "/api/v1/farms/{farm_id}/crop-plan/proposals/{proposal_id}/dismiss",
        "/api/v1/farms/{farm_id}/crop-plan/export",
        # Phase 9 Scenario Explorer endpoints
        "/api/v1/farms/{farm_id}/scenario",
        "/api/v1/farms/{farm_id}/scenarios",
    }
    assert schema["info"]["version"] == "1.0.0"
    assert set(schema["paths"]) == expected_paths
    assert schema["components"]["securitySchemes"]["BearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": "JWT bearer token from identity provider",
    }

    expected_operations = {
        "/api/v1/me": {"get", "patch"},
        "/api/v1/farms": {"get", "post"},
        "/api/v1/farms/{farm_id}": {"get", "patch", "delete"},
        "/api/v1/farms/{farm_id}/decision-support": {"post"},
        "/api/v1/farms/{farm_id}/analysis-jobs": {"post"},
        # Phase 6 Crop Simulator
        "/api/v1/farms/{farm_id}/simulate-crop": {"post"},
        # Phase 7 Risk Center
        "/api/v1/farms/{farm_id}/risks": {"get"},
        "/api/v1/farms/{farm_id}/actions/{action_id}": {"patch"},
        # Phase 8 Annual Planner
        "/api/v1/farms/{farm_id}/crop-plan": {"get"},
        "/api/v1/farms/{farm_id}/crop-plan/entries": {"post"},
        "/api/v1/farms/{farm_id}/crop-plan/entries/{entry_id}": {"patch", "delete"},
        "/api/v1/farms/{farm_id}/crop-plan/proposals/{proposal_id}/accept": {"post"},
        "/api/v1/farms/{farm_id}/crop-plan/proposals/{proposal_id}/dismiss": {"post"},
        "/api/v1/farms/{farm_id}/crop-plan/export": {"get"},
        # Phase 9 Scenario Explorer
        "/api/v1/farms/{farm_id}/scenario": {"post"},
        "/api/v1/farms/{farm_id}/scenarios": {"get"},
    }
    assert {"FarmCreate", "FarmUpdate", "FarmDetailResponse"} <= set(
        schema["components"]["schemas"]
    )

    for path, path_item in schema["paths"].items():
        operations = {
            name
            for name in path_item
            if name in {"get", "post", "put", "patch", "delete"}
        }
        assert operations == expected_operations.get(path, {"get"})
        if path.startswith("/api/v1"):
            for operation_name in operations:
                operation = path_item[operation_name]
                assert operation["security"] == [{"BearerAuth": []}]
                assert {"401", "403", "404", "422", "503"} <= set(
                    operation["responses"]
                )
                for status_code in ("401", "403", "404", "422", "503"):
                    content = operation["responses"][status_code]["content"]
                    assert content["application/json"]["schema"]["$ref"].endswith(
                        "/ErrorResponse"
                    )
        else:
            assert "security" not in path_item["get"]


@pytest.mark.asyncio
async def test_decision_support_route_returns_planner_risks_and_scenario(contract_app):
    response = await _request(
        contract_app,
        "POST",
        f"/api/v1/farms/{OWNED_FARM_ID}/decision-support",
        json={
            "selected_month": 4,
            "rainfall_change_pct": -25,
            "temperature_change_c": 2,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["farm_id"] == str(OWNED_FARM_ID)
    assert body["selected_month"]["month"] == "April"
    assert len(body["months"]) == 12
    assert len(body["selected_month"]["recommendations"]) == 3
    assert {risk["slug"] for risk in body["risks"]} == {
        "drought",
        "heavy-rain",
        "heat",
    }
    assert next(risk for risk in body["risks"] if risk["slug"] == "heavy-rain")[
        "level"
    ] == "Unknown"


@pytest.mark.asyncio
async def test_missing_bearer_token_is_401_even_without_browser_origin():
    app = create_app(_settings(oidc=True))
    app.state.session_factory = object()
    async def factory_override():
        return object()

    app.dependency_overrides[get_app_session_factory] = factory_override
    response = await _request(app, "GET", "/api/v1/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_TOKEN_MISSING"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("POST", "/api/v1/farms", {"name": "Farm", "geometry": {}}),
        ("PATCH", f"/api/v1/farms/{OWNED_FARM_ID}", {"name": "Farm"}),
        ("DELETE", f"/api/v1/farms/{OWNED_FARM_ID}", None),
    ],
)
async def test_farm_write_routes_require_authentication(method, path, json_body):
    app = create_app(_settings(oidc=True))
    app.state.session_factory = object()
    response = await _request(app, method, path, json=json_body)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_TOKEN_MISSING"


@pytest.mark.asyncio
async def test_profile_serializes_exact_utc_and_explicit_nulls(contract_app):
    response = await _request(contract_app, "GET", "/api/v1/me")
    assert response.status_code == 200
    body = response.json()
    assert body["created_at"] == "2025-06-01T06:15:00.123456Z"
    assert body["updated_at"] is None
    assert body["email"] is None
    assert body["display_name"] is None


@pytest.mark.asyncio
async def test_farm_pagination_bounds_order_and_scoped_count(contract_app):
    response = await _request(
        contract_app, "GET", "/api/v1/farms", params={"limit": 2, "offset": 1}
    )
    assert response.status_code == 200
    body = response.json()
    assert [item["name"] for item in body["items"]] == ["Beta", "Gamma"]
    assert body["limit"] == 2
    assert body["offset"] == 1
    assert body["total"] == 3
    assert contract_app.state.contract_service.list_call == (2, 1, "created_at")

    for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
        rejected = await _request(
            contract_app, "GET", "/api/v1/farms", params=params
        )
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_owned_farm_includes_current_geometry_and_utc(contract_app):
    response = await _request(contract_app, "GET", f"/api/v1/farms/{OWNED_FARM_ID}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(OWNED_FARM_ID)
    assert body["current_geometry"]["revision"] == 1
    assert body["current_geometry"]["geometry"]["type"] == "Polygon"
    assert body["created_at"].endswith("Z")
    assert body["updated_at"] is None


VALID_GEOMETRY = {
    "type": "Polygon",
    "coordinates": [[[36.8, -1.3], [36.81, -1.3], [36.81, -1.29], [36.8, -1.3]]],
}


@pytest.mark.asyncio
async def test_create_farm_contract_status_location_idempotency_and_headers(contract_app):
    key = uuid4()
    response = await _request(
        contract_app,
        "POST",
        "/api/v1/farms",
        headers={"Idempotency-Key": str(key)},
        json={"name": "Alpha", "geometry": VALID_GEOMETRY},
    )
    assert response.status_code == 201
    assert response.headers["Location"] == f"/api/v1/farms/{OWNED_FARM_ID}"
    assert response.headers["X-Request-ID"]
    assert response.headers["X-FarmTwin-Data-Mode"] == "demonstration"
    assert response.headers["X-FarmTwin-Auth-Mode"] == "local_demo"
    assert contract_app.state.contract_service.create_call[1] == key

    contract_app.state.contract_service.idempotency_match = True
    repeated = await _request(
        contract_app,
        "POST",
        "/api/v1/farms",
        headers={"Idempotency-Key": str(key)},
        json={"name": "Alpha", "geometry": VALID_GEOMETRY},
    )
    assert repeated.status_code == 200


@pytest.mark.asyncio
async def test_patch_delete_and_conflict_contracts(contract_app):
    patched = await _request(
        contract_app,
        "PATCH",
        f"/api/v1/farms/{OWNED_FARM_ID}",
        json={"name": "Renamed"},
    )
    assert patched.status_code == 200
    assert contract_app.state.contract_service.update_call[0] == OWNED_FARM_ID

    contract_app.state.contract_service.write_error = StaleRevisionError()
    stale = await _request(
        contract_app,
        "PATCH",
        f"/api/v1/farms/{OWNED_FARM_ID}",
        json={"geometry": VALID_GEOMETRY, "expected_revision": 1},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "CONFLICT"
    assert stale.json()["error"]["details"][0]["code"] == "STALE_REVISION"

    contract_app.state.contract_service.write_error = IdempotencyConflict()
    conflict = await _request(
        contract_app,
        "POST",
        "/api/v1/farms",
        headers={"Idempotency-Key": str(uuid4())},
        json={"name": "Alpha", "geometry": VALID_GEOMETRY},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["details"][0]["code"] == "IDEMPOTENCY_MISMATCH"

    contract_app.state.contract_service.write_error = None
    deleted = await _request(
        contract_app, "DELETE", f"/api/v1/farms/{OWNED_FARM_ID}"
    )
    assert deleted.status_code == 204
    assert deleted.content == b""


@pytest.mark.asyncio
async def test_write_validation_errors_have_field_details(contract_app):
    invalid_name = await _request(
        contract_app,
        "POST",
        "/api/v1/farms",
        json={"name": "   ", "geometry": VALID_GEOMETRY},
    )
    assert invalid_name.status_code == 422
    assert invalid_name.json()["error"]["details"][0]["field"] == "body.name"

    invalid_update = await _request(
        contract_app,
        "PATCH",
        f"/api/v1/farms/{OWNED_FARM_ID}",
        json={"geometry": VALID_GEOMETRY},
    )
    assert invalid_update.status_code == 422
    assert invalid_update.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_invisible_and_absent_farms_have_equivalent_404_contract(contract_app):
    invisible = await _request(
        contract_app, "GET", f"/api/v1/farms/{INVISIBLE_FARM_ID}"
    )
    absent = await _request(contract_app, "GET", f"/api/v1/farms/{ABSENT_FARM_ID}")
    assert invisible.status_code == absent.status_code == 404
    for response in (invisible, absent):
        error = response.json()["error"]
        assert error["code"] == "RESOURCE_NOT_FOUND"
        assert error["message"] == "Resource not found"
        assert error["details"] == []


@pytest.mark.asyncio
async def test_malformed_uuid_and_unsupported_version_use_common_errors(contract_app):
    malformed = await _request(contract_app, "GET", "/api/v1/farms/not-a-uuid")
    unsupported = await _request(contract_app, "GET", "/api/v2/farms")
    assert malformed.status_code == 422
    assert malformed.json()["error"]["code"] == "VALIDATION_ERROR"
    assert malformed.json()["error"]["details"][0]["field"] == "path.farm_id"
    assert unsupported.status_code == 404
    assert unsupported.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


# ---------------------------------------------------------------------------
# Phase 5 twin contract tests
# ---------------------------------------------------------------------------


class _ScalarResult:
    """Mimics SQLAlchemy ScalarResult for single-row returns."""

    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        if self._value is None:
            raise Exception("No row found")
        return self._value


class _TwinSession:
    """Minimal async session stub for twin route queries.

    Intercepts execute() calls and returns pre-configured responses based
    on the model being queried.  The session stub is set up per test through
    the `farm`, `snapshot`, and `job` attributes.
    """

    def __init__(self, farm=None, snapshot=None, job=None):
        self._farm = farm          # returned for Farm queries
        self._snapshot = snapshot  # returned for AnalysisSnapshot queries
        self._job = job            # returned for AnalysisJob queries
        self._added = []

    async def execute(self, statement):
        # Inspect the statement's entity to decide what to return.
        # SQLAlchemy select() exposes froms or column_descriptions.
        entity = _get_statement_entity(statement)
        if entity is AnalysisSnapshot:
            return _ScalarResult(self._snapshot)
        if entity is AnalysisJob:
            return _ScalarResult(self._job)
        # Default: Farm query
        return _ScalarResult(self._farm)

    def add(self, obj):
        self._added.append(obj)
        # Assign a stable UUID so the route can read job.id
        if isinstance(obj, AnalysisJob) and obj.id is None:
            obj.id = uuid4()

    async def flush(self):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def close(self):
        pass


def _get_statement_entity(statement):
    """Extract the primary ORM entity from a SQLAlchemy select() statement."""
    try:
        # Works for simple select(Model) statements
        cols = statement.column_descriptions
        if cols:
            return cols[0]["entity"]
    except Exception:
        pass
    return None


def _make_farm(farm_id: UUID, user_id: UUID, revision: int = 1) -> object:
    """Build a minimal Farm stub."""
    return SimpleNamespace(
        id=farm_id,
        user_id=user_id,
        name="Test Farm",
        current_geometry_revision=revision,
    )


def _make_job(
    job_id: UUID,
    farm_id: UUID,
    status: JobStatus = JobStatus.QUEUED,
    snapshot_id: UUID | None = None,
) -> object:
    """Build a minimal AnalysisJob stub."""
    return SimpleNamespace(
        id=job_id,
        farm_id=farm_id,
        geometry_revision=1,
        status=status,
        stages={"weather": {"status": "queued"}, "satellite": {"status": "queued"}},
        error_message=None,
        snapshot_id=snapshot_id,
        created_at=datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC),
    )


def _make_snapshot(
    snapshot_id: UUID,
    farm_id: UUID,
    job_id: UUID,
) -> object:
    """Build a minimal AnalysisSnapshot stub with provenance-complete weather."""
    weather_payload = {
        "temperature_2m": {
            "value": 24.3,
            "unit": "celsius",
            "source": "open-meteo",
            "acquired_at": "2026-09-08T06:00:00Z",
            "retrieved_at": "2026-09-08T09:14:33Z",
            "data_mode": "live",
            "quality": "accepted",
            "resolution_m": 11000,
        }
    }
    return SimpleNamespace(
        id=snapshot_id,
        farm_id=farm_id,
        geometry_revision=1,
        job_id=job_id,
        valid_time_utc=datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC),
        data_mode="live",
        evidence_statuses={"weather": "accepted", "satellite": "unavailable"},
        model_version="farmtwin-twin-v1",
        weather=weather_payload,
        climate_baseline=None,
        satellite=None,
        soil=None,
        terrain=None,
        conduit=None,
        created_at=datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC),
    )


def _make_twin_app(*, farm=None, snapshot=None, job=None):
    """Create a test app with twin session and principal overrides."""
    app = create_app(_settings())
    principal = Principal(
        user_id=USER_ID,
        issuer="https://issuer.example",
        subject="subject-1",
        permissions=frozenset(),
    )
    twin_session = _TwinSession(farm=farm, snapshot=snapshot, job=job)

    async def principal_override():
        return principal

    async def session_override():
        yield twin_session

    app.dependency_overrides[get_current_principal] = principal_override
    app.dependency_overrides[get_request_session] = session_override
    return app


@pytest.mark.asyncio
async def test_post_analysis_jobs_returns_202_and_location():
    """POST analysis-jobs → 202 Accepted + Location header.

    Requirements: 10.2
    """
    farm_id = OWNED_FARM_ID
    farm = _make_farm(farm_id, USER_ID)
    app = _make_twin_app(farm=farm)

    response = await _request(app, "POST", f"/api/v1/farms/{farm_id}/analysis-jobs")

    assert response.status_code == 202
    assert "Location" in response.headers
    location = response.headers["Location"]
    assert location.startswith("/api/v1/jobs/")
    # The job ID in the Location header must be a valid UUID
    job_id_str = location.removeprefix("/api/v1/jobs/")
    UUID(job_id_str)  # raises if not valid UUID


@pytest.mark.asyncio
async def test_get_digital_twin_unavailable_when_no_snapshot_or_job():
    """GET digital-twin with no snapshot and no job → {status: "unavailable"}.

    Requirements: 10.1
    """
    farm_id = OWNED_FARM_ID
    farm = _make_farm(farm_id, USER_ID)
    # No snapshot, no pending job
    app = _make_twin_app(farm=farm, snapshot=None, job=None)

    response = await _request(app, "GET", f"/api/v1/farms/{farm_id}/digital-twin")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unavailable"
    assert body.get("job_id") is None
    assert body.get("snapshot_id") is None


@pytest.mark.asyncio
async def test_get_digital_twin_pending_when_job_running():
    """GET digital-twin with a queued job but no snapshot → {status: "pending", job_id: ...}.

    Requirements: 10.1
    """
    farm_id = OWNED_FARM_ID
    job_id = uuid4()
    farm = _make_farm(farm_id, USER_ID)
    job = _make_job(job_id, farm_id, status=JobStatus.QUEUED)
    # No snapshot yet
    app = _make_twin_app(farm=farm, snapshot=None, job=job)

    response = await _request(app, "GET", f"/api/v1/farms/{farm_id}/digital-twin")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["job_id"] == str(job_id)


@pytest.mark.asyncio
async def test_get_digital_twin_ready_with_full_provenance():
    """GET digital-twin with completed snapshot → status "ready" with provenance.

    Requirements: 10.1, 10.4, 7.4
    """
    farm_id = OWNED_FARM_ID
    job_id = uuid4()
    snapshot_id = uuid4()
    farm = _make_farm(farm_id, USER_ID)
    snapshot = _make_snapshot(snapshot_id, farm_id, job_id)
    app = _make_twin_app(farm=farm, snapshot=snapshot)

    response = await _request(app, "GET", f"/api/v1/farms/{farm_id}/digital-twin")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["snapshot_id"] == str(snapshot_id)
    assert body["farm_id"] == str(farm_id)
    assert body["geometry_revision"] == 1
    assert body["data_mode"] == "live"
    assert body["model_version"] == "farmtwin-twin-v1"
    assert body["valid_time"] is not None
    # Evidence statuses must be present
    assert body["evidence_statuses"] == {"weather": "accepted", "satellite": "unavailable"}
    # Weather payload with provenance fields
    assert body["weather"]["temperature_2m"]["value"] == 24.3
    assert body["weather"]["temperature_2m"]["source"] == "open-meteo"
    assert body["weather"]["temperature_2m"]["data_mode"] == "live"
    assert body["weather"]["temperature_2m"]["quality"] == "accepted"
    assert body["weather"]["temperature_2m"]["acquired_at"] is not None
    assert body["weather"]["temperature_2m"]["retrieved_at"] is not None


@pytest.mark.asyncio
async def test_get_digital_twin_wrong_owner_returns_404():
    """GET digital-twin for a farm not owned by the user → 404, same as non-existent.

    Requirements: 10.3
    """
    # Other user's farm — session returns no farm (ownership check fails)
    app = _make_twin_app(farm=None)

    # Wrong-owner farm: session returns nothing (as if invisible)
    invisible_response = await _request(
        app, "GET", f"/api/v1/farms/{INVISIBLE_FARM_ID}/digital-twin"
    )
    # Absent farm: same app, same session behaviour
    absent_response = await _request(
        app, "GET", f"/api/v1/farms/{ABSENT_FARM_ID}/digital-twin"
    )

    assert invisible_response.status_code == absent_response.status_code == 404
    for r in (invisible_response, absent_response):
        error = r.json()["error"]
        assert error["code"] == "RESOURCE_NOT_FOUND"
        assert error["message"] == "Resource not found"
        assert error["details"] == []


@pytest.mark.asyncio
async def test_post_analysis_jobs_wrong_owner_returns_404():
    """POST analysis-jobs for a farm not owned by the user → 404.

    Requirements: 10.3
    """
    app = _make_twin_app(farm=None)

    response = await _request(
        app, "POST", f"/api/v1/farms/{INVISIBLE_FARM_ID}/analysis-jobs"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_digital_twin_response_includes_standard_headers():
    """GET digital-twin returns standard X-Request-ID and data-mode headers.

    Requirements: 10.1
    """
    farm_id = OWNED_FARM_ID
    farm = _make_farm(farm_id, USER_ID)
    app = _make_twin_app(farm=farm, snapshot=None, job=None)

    response = await _request(app, "GET", f"/api/v1/farms/{farm_id}/digital-twin")

    assert response.status_code == 200
    assert response.headers.get("X-Request-ID")
    assert response.headers.get("X-FarmTwin-Data-Mode") == "demonstration"
    assert response.headers.get("X-FarmTwin-Auth-Mode") == "local_demo"
