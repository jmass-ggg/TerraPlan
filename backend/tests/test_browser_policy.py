"""Mandatory CORS, browser-authentication, and trusted-proxy policy tests.

Feature: backend-foundation
Property 14: Browser and release security, Phase 1 portion
Requirements: 13.1, 13.2, 13.3, 14.3
"""

from __future__ import annotations

from typing import Annotated

import pytest
from fastapi import FastAPI, Header, Request
from httpx import ASGITransport, AsyncClient
from hypothesis import HealthCheck, given, settings as hypothesis_settings
from hypothesis import strategies as st
from pydantic import ValidationError

from app.core.browser_policy import (
    CORS_ALLOWED_HEADERS,
    CORS_ALLOWED_METHODS,
    CORS_EXPOSED_HEADERS,
    apply_browser_policy,
)
from app.core.config import Settings
from app.core.exceptions import AuthenticationError, install_exception_handlers
from app.core.request_context import RequestContextMiddleware


ALLOWED_ORIGIN = "https://ui.example"


def _settings(*, trusted_ips: list[str] | None = None) -> Settings:
    return Settings(
        environment="development",
        data_mode="demonstration",
        auth={"mode": "local_demo"},
        demo={"local_only": True, "isolated_database": True},
        cors={"origins": [ALLOWED_ORIGIN]},
        proxy={"trusted_ips": trusted_ips or []},
    )


def _create_policy_app(settings: Settings):
    api = FastAPI()
    api.state.settings = settings
    install_exception_handlers(api)
    return RequestContextMiddleware(apply_browser_policy(api, settings), settings)


@pytest.fixture
def browser_app():
    app = _create_policy_app(_settings())

    @app.get("/protected")
    async def protected(
        authorization: Annotated[str | None, Header()] = None,
    ):
        if authorization != "Bearer valid-test-token":
            raise AuthenticationError("Missing token", "AUTH_TOKEN_MISSING")
        return {"ok": True}

    return app


async def _request(app, method: str, path: str, **kwargs):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.request(method, path, **kwargs)


@pytest.mark.asyncio
async def test_listed_origin_gets_exact_grant_and_readable_context_headers(
    browser_app,
):
    response = await _request(
        browser_app,
        "GET",
        "/protected",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Authorization": "Bearer valid-test-token",
        },
    )

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN
    assert "Access-Control-Allow-Credentials" not in response.headers
    exposed = {
        name.strip().lower()
        for name in response.headers["Access-Control-Expose-Headers"].split(",")
    }
    assert exposed == {name.lower() for name in CORS_EXPOSED_HEADERS}
    assert response.headers["X-Request-ID"]
    assert response.headers["X-FarmTwin-Auth-Mode"] == "local_demo"
    assert response.headers["X-FarmTwin-Data-Mode"] == "demonstration"


@pytest.mark.asyncio
async def test_cors_grant_never_replaces_protected_route_authentication(browser_app):
    response = await _request(
        browser_app,
        "GET",
        "/protected",
        headers={"Origin": ALLOWED_ORIGIN},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_TOKEN_MISSING"
    assert response.headers["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.asyncio
@given(host=st.from_regex(r"[a-z]{1,16}", fullmatch=True))
@hypothesis_settings(
    max_examples=30,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
async def test_property_unlisted_origins_receive_no_cross_origin_grant(
    browser_app,
    host: str,
):
    origin = f"https://{host}.invalid"
    response = await _request(
        browser_app,
        "GET",
        "/protected",
        headers={
            "Origin": origin,
            "Authorization": "Bearer valid-test-token",
        },
    )

    assert response.status_code == 200
    assert "Access-Control-Allow-Origin" not in response.headers
    assert "Access-Control-Allow-Credentials" not in response.headers


@pytest.mark.asyncio
async def test_allowed_preflight_has_only_intended_methods_and_headers(browser_app):
    response = await _request(
        browser_app,
        "OPTIONS",
        "/protected",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": (
                "Authorization, Content-Type, X-Idempotency-Key"
            ),
        },
    )

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN
    assert "Access-Control-Allow-Credentials" not in response.headers
    assert response.headers["Access-Control-Allow-Methods"].split(", ") == list(
        CORS_ALLOWED_METHODS
    )
    allowed_headers = {
        name.strip().lower()
        for name in response.headers["Access-Control-Allow-Headers"].split(",")
    }
    assert {name.lower() for name in CORS_ALLOWED_HEADERS} <= allowed_headers
    assert "idempotency-key" in allowed_headers
    assert "x-user-id" not in allowed_headers
    assert response.headers["X-Request-ID"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("origin", "method", "requested_headers", "code", "has_origin_grant"),
    [
        (
            "https://unlisted.example",
            "GET",
            "Authorization",
            "CORS_ORIGIN_DENIED",
            False,
        ),
        (
            ALLOWED_ORIGIN,
            "PUT",
            "Authorization",
            "CORS_PREFLIGHT_DENIED",
            True,
        ),
        (
            ALLOWED_ORIGIN,
            "GET",
            "X-User-ID",
            "CORS_PREFLIGHT_DENIED",
            True,
        ),
    ],
)
async def test_rejected_preflights_use_shared_error_and_request_context(
    browser_app,
    origin,
    method,
    requested_headers,
    code,
    has_origin_grant,
):
    response = await _request(
        browser_app,
        "OPTIONS",
        "/protected",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": requested_headers,
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == code
    assert (
        response.headers.get("Access-Control-Allow-Origin") == ALLOWED_ORIGIN
    ) is has_origin_grant
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
    assert response.headers["X-FarmTwin-Auth-Mode"] == "local_demo"
    assert response.headers["X-FarmTwin-Data-Mode"] == "demonstration"


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "null",
        "https://user:password@ui.example",
        "https://ui.example/",
        "https://ui.example/path",
        "https://ui.example?query=yes",
        "https://ui.example#fragment",
        "ftp://ui.example",
        " https://ui.example",
    ],
)
def test_cors_configuration_rejects_non_origin_values(origin):
    with pytest.raises(ValidationError):
        Settings(
            environment="development",
            data_mode="demonstration",
            auth={"mode": "local_demo"},
            demo={"local_only": True, "isolated_database": True},
            cors={"origins": [origin]},
        )


@pytest.mark.asyncio
async def test_forwarded_metadata_changes_scope_only_for_configured_proxy():
    trusted_app = _create_policy_app(_settings(trusted_ips=["127.0.0.1"]))

    @trusted_app.get("/scope")
    async def trusted_scope(request: Request):
        return f"{request.scope['scheme']}|{request.scope['client'][0]}"

    trusted = await _request(
        trusted_app,
        "GET",
        "/scope",
        headers={
            "X-Forwarded-Proto": "https",
            "X-Forwarded-For": "203.0.113.9",
        },
    )
    assert trusted.json() == "https|203.0.113.9"

    untrusted_app = _create_policy_app(_settings(trusted_ips=["192.0.2.10"]))

    @untrusted_app.get("/scope")
    async def untrusted_scope(request: Request):
        return f"{request.scope['scheme']}|{request.scope['client'][0]}"

    untrusted = await _request(
        untrusted_app,
        "GET",
        "/scope",
        headers={
            "X-Forwarded-Proto": "https",
            "X-Forwarded-For": "203.0.113.9",
        },
    )
    assert untrusted.json() == "http|127.0.0.1"


@pytest.mark.parametrize("trusted_ip", ["*", "proxy.example", "10.0.0.1/24"])
def test_trusted_proxy_configuration_fails_closed(trusted_ip):
    with pytest.raises(ValidationError):
        _settings(trusted_ips=[trusted_ip])
