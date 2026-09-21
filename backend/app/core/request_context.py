"""Outer ASGI request context, response headers and completion diagnostics."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import Settings
from .logging import request_log_fields


logger = logging.getLogger("farmtwin.request")


@dataclass(frozen=True)
class RequestContext:
    request_id: str


_request_context: ContextVar[RequestContext | None] = ContextVar(
    "farmtwin_request_context", default=None
)
_request_error_code: ContextVar[str | None] = ContextVar(
    "farmtwin_request_error_code", default=None
)


def get_request_context() -> RequestContext | None:
    return _request_context.get()


def get_request_id() -> str | None:
    context = get_request_context()
    return context.request_id if context else None


def set_request_error_code(code: str) -> None:
    """Attach a stable normalized error code to the current request log."""

    _request_error_code.set(code)


def get_request_error_code() -> str | None:
    return _request_error_code.get()


def _route_template(scope: Scope) -> str:
    route = scope.get("route")
    template = getattr(route, "path", None)
    if isinstance(template, str) and template.startswith("/"):
        return template
    return "<unmatched>"


class RequestContextMiddleware:
    """Generate server-owned request IDs around the entire HTTP stack."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    def __getattr__(self, name: str) -> Any:
        # Preserve FastAPI's useful surface (routes, decorators, state, OpenAPI)
        # while keeping this context layer physically outermost.
        return getattr(self.app, name)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid4())
        token = _request_context.set(RequestContext(request_id=request_id))
        error_code_token = _request_error_code.set(None)
        started_at = time.monotonic()
        method = str(scope.get("method", "UNKNOWN")).upper()
        status = 500
        response_started = False
        disconnected = False
        outcome = "completed"

        async def receive_with_disconnect() -> Message:
            nonlocal disconnected
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
            return message

        async def send_with_context(message: Message) -> None:
            nonlocal response_started, status
            if message["type"] == "http.response.start":
                response_started = True
                status = int(message["status"])
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                headers["X-FarmTwin-Data-Mode"] = self.settings.data_mode.value
                headers["X-FarmTwin-Auth-Mode"] = self.settings.auth.mode.value
            await send(message)

        try:
            await self.app(scope, receive_with_disconnect, send_with_context)
            if disconnected:
                outcome = "disconnected"
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception as exc:
            outcome = "error"
            if not response_started:
                from .exceptions import error_response

                response = error_response(
                    status_code=500,
                    code="INTERNAL_ERROR",
                    message="An internal error occurred",
                )
                await response(scope, receive_with_disconnect, send_with_context)
            # Starlette deliberately re-raises after its 500 handler.  At this
            # outer boundary the response has already been safely normalized.
            logger.error(
                "request.failed",
                extra=request_log_fields(
                    self.settings,
                    event="request.failed",
                    error_code="INTERNAL_ERROR",
                    request_id=request_id,
                    method=method,
                    route=_route_template(scope),
                    status=status,
                    exception_type=type(exc).__name__,
                ),
            )
        finally:
            duration_ms = (time.monotonic() - started_at) * 1000
            level = logging.ERROR if outcome in {"error", "cancelled"} else logging.INFO
            logger.log(
                level,
                "request.completed",
                extra=request_log_fields(
                    self.settings,
                    event="request.completed",
                    error_code=get_request_error_code(),
                    request_id=request_id,
                    method=method,
                    route=_route_template(scope),
                    status=status,
                    duration_ms=duration_ms,
                    outcome=outcome,
                ),
            )
            _request_error_code.reset(error_code_token)
            _request_context.reset(token)


def internal_error_body(request_id: str) -> bytes:
    """Small helper for ASGI-level emergency responses."""

    return json.dumps(
        {
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An internal error occurred",
                "request_id": request_id,
                "details": [],
            }
        },
        separators=(",", ":"),
    ).encode("utf-8")
