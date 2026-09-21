"""Exact-origin browser policy and trusted reverse-proxy composition."""

from __future__ import annotations

from typing import Any

from fastapi.middleware.cors import CORSMiddleware
from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.types import ASGIApp
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from .config import Settings
from .exceptions import error_response


CORS_ALLOWED_METHODS = ("GET", "POST", "PATCH", "DELETE", "OPTIONS")
CORS_ALLOWED_HEADERS = (
    "Authorization",
    "Content-Type",
    "Idempotency-Key",
    # Kept for compatibility with the Phase 1 browser contract. New clients
    # use the standards-shaped Idempotency-Key header.
    "X-Idempotency-Key",
)
CORS_EXPOSED_HEADERS = (
    "X-Request-ID",
    "X-FarmTwin-Auth-Mode",
    "X-FarmTwin-Data-Mode",
)


class SafeCORSMiddleware(CORSMiddleware):
    """Keep rejected browser preflights inside the common error contract."""

    def __getattr__(self, name: str) -> Any:
        return getattr(self.app, name)

    def preflight_response(self, request_headers: Headers) -> Response:
        response = super().preflight_response(request_headers)
        if response.status_code < 400:
            return response

        origin_allowed = self.is_allowed_origin(request_headers["origin"])
        normalized = error_response(
            status_code=403,
            code=(
                "CORS_ORIGIN_DENIED"
                if not origin_allowed
                else "CORS_PREFLIGHT_DENIED"
            ),
            message=(
                "Browser origin is not allowed"
                if not origin_allowed
                else "Browser preflight is not allowed"
            ),
        )
        for name, value in response.headers.items():
            if name.lower().startswith("access-control-") or name.lower() == "vary":
                normalized.headers[name] = value
        return normalized


class TrustedProxyHeadersMiddleware(ProxyHeadersMiddleware):
    """Apply forwarding metadata only for configured peers and preserve app APIs."""

    def __getattr__(self, name: str) -> Any:
        return getattr(self.app, name)


def apply_browser_policy(app: ASGIApp, settings: Settings) -> ASGIApp:
    """Wrap an ASGI app with exact CORS and application-owned proxy trust."""
    cors = SafeCORSMiddleware(
        app,
        allow_origins=settings.cors.origins,
        allow_credentials=False,
        allow_methods=CORS_ALLOWED_METHODS,
        allow_headers=CORS_ALLOWED_HEADERS,
        expose_headers=CORS_EXPOSED_HEADERS,
    )
    # Uvicorn's own proxy-header processing must be disabled at launch. This
    # application-owned layer changes client/scheme only for configured peers.
    return TrustedProxyHeadersMiddleware(
        cors,
        trusted_hosts=settings.proxy.trusted_ips,
    )
