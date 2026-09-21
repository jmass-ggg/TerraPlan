"""Mandatory error, request-context and privacy diagnostics tests.

Requirements: 1.5, 3.5, 6.1-6.8, 9.1-9.7, 12.2, 14.3
Properties 7 and 8.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging

import pytest
from fastapi import Body, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.core.exceptions import (
    AuthenticationError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.core.logging import PrivacyFilter, PrivacyJSONFormatter
from app.core.request_context import (
    RequestContextMiddleware,
    get_request_context,
    get_request_id,
)
from app.main import create_app


SECRET = "SECRET_CANARY_7cf81"
GEOMETRY = "POLYGON_CANARY_82a4"
LOCATION = "LOCATION_CANARY_91bd"
PROFILE = "PROFILE_CANARY_a38e"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="development",
        data_mode="demonstration",
        auth={"mode": "local_demo"},
        demo={"local_only": True, "isolated_database": True},
        cors={"origins": ["https://ui.example"]},
    )


class InputModel(BaseModel):
    count: int
    nested: dict[str, str]


@pytest.fixture
def boundary_app(settings: Settings):
    app = create_app(settings)

    @app.get("/success")
    async def success():
        return {"request_id": get_request_id()}

    @app.get("/auth")
    async def auth():
        raise AuthenticationError(SECRET, "AUTH_TOKEN_MISSING")

    @app.get("/forbidden")
    async def forbidden():
        raise ForbiddenError(PROFILE)

    @app.get("/missing")
    async def missing():
        raise NotFoundError(LOCATION)

    @app.get("/unavailable")
    async def unavailable():
        raise ServiceUnavailableError(SECRET)

    @app.get("/unexpected")
    async def unexpected():
        raise RuntimeError(f"{SECRET} {GEOMETRY} {LOCATION} {PROFILE}")

    @app.get("/database")
    async def database():
        raise OperationalError(
            f"SELECT * FROM private WHERE token='{SECRET}'",
            {"geometry": GEOMETRY, "location": LOCATION},
            RuntimeError(PROFILE),
        )

    @app.post("/validated")
    async def validated(payload: InputModel = Body()):
        return payload

    @app.get("/protocol")
    async def protocol():
        raise HTTPException(
            503,
            detail=SECRET,
            headers={
                "Retry-After": "12",
                "X-Unsafe-Secret": SECRET,
            },
        )

    @app.get("/logged")
    async def logged():
        logging.getLogger("farmtwin.test.boundary").debug(
            f"body={SECRET}",
            extra={"event": "dependency.failed", "geometry": GEOMETRY},
        )
        return {"ok": True}

    return app


async def _request(app, method: str, path: str, **kwargs):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.request(method, path, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/auth", 401, "AUTH_TOKEN_MISSING"),
        ("/forbidden", 403, "AUTH_INSUFFICIENT_PERMISSIONS"),
        ("/missing", 404, "RESOURCE_NOT_FOUND"),
        ("/unavailable", 503, "SERVICE_UNAVAILABLE"),
        ("/database", 503, "SERVICE_UNAVAILABLE"),
        ("/unexpected", 500, "INTERNAL_ERROR"),
    ],
)
async def test_common_errors_are_sanitized(boundary_app, path, status, code):
    response = await _request(boundary_app, "GET", path)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["details"] == []
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
    assert not any(
        canary in response.text for canary in (SECRET, GEOMETRY, LOCATION, PROFILE)
    )


@pytest.mark.asyncio
async def test_framework_404_and_405_use_envelope_and_preserve_allow(boundary_app):
    missing = await _request(boundary_app, "GET", f"/unknown/{LOCATION}")
    method = await _request(boundary_app, "DELETE", "/success")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert LOCATION not in missing.text
    assert method.status_code == 405
    assert method.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert "GET" in method.headers["Allow"]


@pytest.mark.asyncio
async def test_validation_details_never_echo_nested_input(boundary_app):
    response = await _request(
        boundary_app,
        "POST",
        "/validated",
        json={
            "count": SECRET,
            "nested": {"geometry": GEOMETRY, "profile": PROFILE},
        },
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["details"] == [
        {
            "field": "body.count",
            "code": "INVALID_TYPE",
            "message": "Value has an invalid type",
        }
    ]
    assert SECRET not in response.text


@pytest.mark.asyncio
async def test_safe_protocol_headers_only(boundary_app):
    auth = await _request(boundary_app, "GET", "/auth")
    dependency = await _request(boundary_app, "GET", "/protocol")
    assert auth.headers["WWW-Authenticate"] == "Bearer"
    assert dependency.headers["Retry-After"] == "12"
    assert "X-Unsafe-Secret" not in dependency.headers


@pytest.mark.asyncio
async def test_success_and_concurrent_request_contexts(boundary_app):
    responses = await asyncio.gather(
        *[_request(boundary_app, "GET", "/success") for _ in range(20)]
    )
    ids = [response.headers["X-Request-ID"] for response in responses]
    assert len(set(ids)) == len(ids)
    for response in responses:
        assert response.status_code == 200
        assert response.json()["request_id"] == response.headers["X-Request-ID"]
        assert response.headers["X-FarmTwin-Data-Mode"] == "demonstration"
        assert response.headers["X-FarmTwin-Auth-Mode"] == "local_demo"

    supplied = "00000000-0000-4000-8000-000000000000"
    server_owned = await _request(
        boundary_app, "GET", "/success", headers={"X-Request-ID": supplied}
    )
    assert server_owned.headers["X-Request-ID"] != supplied
    assert server_owned.json()["request_id"] == server_owned.headers["X-Request-ID"]
    assert get_request_context() is None


@pytest.mark.asyncio
async def test_cors_preflights_have_request_context(boundary_app):
    allowed = await _request(
        boundary_app,
        "OPTIONS",
        "/success",
        headers={
            "Origin": "https://ui.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    denied = await _request(
        boundary_app,
        "OPTIONS",
        "/success",
        headers={
            "Origin": f"https://{SECRET}.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert allowed.status_code == 200
    assert allowed.headers["X-Request-ID"]
    assert denied.status_code == 403
    assert denied.json()["error"]["request_id"] == denied.headers["X-Request-ID"]
    assert SECRET not in denied.text


def test_production_formatter_has_allowlisted_json_only():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(PrivacyFilter())
    handler.setFormatter(PrivacyJSONFormatter())
    test_logger = logging.getLogger("test.privacy.formatter")
    test_logger.handlers = [handler]
    test_logger.propagate = False
    test_logger.setLevel(logging.DEBUG)

    try:
        raise RuntimeError(SECRET)
    except RuntimeError:
        test_logger.exception(
            f"body={SECRET}",
            extra={
                "event": "request.failed",
                "request_id": "00000000-0000-4000-8000-000000000001",
                "method": "GET",
                "route": "/farms/{farm_id}",
                "status": 500,
                "duration_ms": 1.25,
                "authorization": SECRET,
                "geometry": GEOMETRY,
                "profile": PROFILE,
                "sql": f"SELECT '{SECRET}'",
            },
        )

    output = stream.getvalue()
    parsed = json.loads(output)
    assert parsed["event"] == "request.failed"
    assert parsed["duration_ms"] == 1.25
    assert set(parsed) <= {
        "time",
        "severity",
        "event",
        "error_code",
        "request_id",
        "method",
        "route",
        "status",
        "duration_ms",
        "environment",
        "auth_mode",
        "data_mode",
        "outcome",
        "exception_type",
    }
    assert not any(
        canary in output for canary in (SECRET, GEOMETRY, LOCATION, PROFILE)
    )


def test_startup_debug_diagnostics_obey_same_privacy_rules():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(PrivacyFilter())
    handler.setFormatter(PrivacyJSONFormatter())
    startup_logger = logging.getLogger("test.privacy.startup")
    startup_logger.handlers = [handler]
    startup_logger.propagate = False
    startup_logger.setLevel(logging.DEBUG)
    startup_logger.debug(
        f"startup config={SECRET}",
        extra={
            "event": "startup.failed",
            "credential": SECRET,
            "signed_url": f"https://example.invalid/?token={SECRET}",
            "profile": PROFILE,
        },
    )
    output = stream.getvalue()
    assert json.loads(output)["event"] == "startup.failed"
    assert SECRET not in output
    assert PROFILE not in output


@pytest.mark.asyncio
async def test_application_logs_inherit_request_id_at_debug(boundary_app):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(PrivacyFilter())
    handler.setFormatter(PrivacyJSONFormatter())
    app_logger = logging.getLogger("farmtwin.test.boundary")
    old_handlers, old_propagate, old_level = (
        app_logger.handlers,
        app_logger.propagate,
        app_logger.level,
    )
    app_logger.handlers = [handler]
    app_logger.propagate = False
    app_logger.setLevel(logging.DEBUG)
    try:
        response = await _request(boundary_app, "GET", "/logged")
    finally:
        app_logger.handlers = old_handlers
        app_logger.propagate = old_propagate
        app_logger.setLevel(old_level)

    output = stream.getvalue()
    assert json.loads(output)["request_id"] == response.headers["X-Request-ID"]
    assert SECRET not in output
    assert GEOMETRY not in output


@pytest.mark.asyncio
async def test_cancellation_resets_context_and_is_reraised(settings):
    entered = asyncio.Event()

    async def blocked(scope, receive, send):
        entered.set()
        await asyncio.Event().wait()

    middleware = RequestContextMiddleware(blocked, settings)
    scope = {"type": "http", "method": "GET", "path": f"/{SECRET}"}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    task = asyncio.create_task(middleware(scope, receive, send))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert get_request_context() is None


@pytest.mark.asyncio
async def test_disconnect_is_recorded_without_path_leak(settings):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(PrivacyFilter())
    handler.setFormatter(PrivacyJSONFormatter())
    request_logger = logging.getLogger("farmtwin.request")
    old_handlers, old_propagate = request_logger.handlers, request_logger.propagate
    request_logger.handlers = [handler]
    request_logger.propagate = False
    request_logger.setLevel(logging.INFO)

    async def disconnected_app(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestContextMiddleware(disconnected_app, settings)

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        pass

    try:
        await middleware(
            {"type": "http", "method": "GET", "path": f"/{LOCATION}"},
            receive,
            send,
        )
    finally:
        request_logger.handlers = old_handlers
        request_logger.propagate = old_propagate

    output = stream.getvalue()
    assert '"outcome":"disconnected"' in output
    assert LOCATION not in output
    assert get_request_context() is None
