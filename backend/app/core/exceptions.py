"""Stable, privacy-safe application exceptions and FastAPI handlers.

Exception values are useful inside the domain, but they are never copied into an
HTTP response.  The boundary translates every supported failure into the single
``ErrorResponse`` schema defined in :mod:`app.api.schemas`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.schemas import ErrorDetail, ErrorEnvelope, ErrorResponse

from .request_context import get_request_id, set_request_error_code


SAFE_PROTOCOL_HEADERS = frozenset({"www-authenticate", "allow", "retry-after"})
_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_SAFE_FIELD_PART = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")


class ApplicationError(Exception):
    """Base for deliberate failures with a stable public representation."""

    status_code = 500
    code = "INTERNAL_ERROR"
    public_message = "An internal error occurred"

    def __init__(
        self,
        internal_message: str | None = None,
        *,
        headers: Mapping[str, str] | None = None,
        details: list[ErrorDetail] | None = None,
    ) -> None:
        super().__init__(internal_message or self.public_message)
        self.headers = dict(headers or {})
        self.details = list(details or [])
        # Compatibility with code that previously consumed HTTPException.detail.
        self.detail = {
            "code": self.code,
            "message": internal_message or self.public_message,
        }


class DomainException(ApplicationError):
    """Base for expected domain and repository outcomes."""


class NotFoundError(DomainException):
    status_code = 404
    code = "RESOURCE_NOT_FOUND"
    public_message = "Resource not found"


class ForbiddenError(DomainException):
    status_code = 403
    code = "AUTH_INSUFFICIENT_PERMISSIONS"
    public_message = "Insufficient permissions"


class ConflictError(DomainException):
    status_code = 409
    code = "CONFLICT"
    public_message = "The request conflicts with current state"


class FarmValidationError(DomainException):
    """Authoritative farm validation failure with field-level context."""

    status_code = 422
    code = "VALIDATION_ERROR"
    public_message = "Invalid farm data"

    def __init__(self, field: str, detail_code: str, message: str) -> None:
        super().__init__(
            message,
            details=[ErrorDetail(field=field, code=detail_code, message=message)],
        )


class IdempotencyConflict(ConflictError):
    """An idempotency key was reused for a different create payload."""

    def __init__(self) -> None:
        super().__init__(
            "Idempotency key payload mismatch",
            details=[
                ErrorDetail(
                    field="header.Idempotency-Key",
                    code="IDEMPOTENCY_MISMATCH",
                    message="This idempotency key was already used for a different farm",
                )
            ],
        )


class StaleRevisionError(ConflictError):
    """A geometry write lost an optimistic-concurrency race."""

    def __init__(self) -> None:
        super().__init__(
            "Farm boundary was updated elsewhere",
            details=[
                ErrorDetail(
                    field="body.expected_revision",
                    code="STALE_REVISION",
                    message="Reload the latest farm boundary before saving again",
                )
            ],
        )


class FarmDeleteConflict(ConflictError):
    """Deletion is blocked by retained resources."""

    def __init__(self, blocking_resources: list[str]) -> None:
        resources = ", ".join(sorted(blocking_resources))
        super().__init__(
            "Farm has resources that prevent deletion",
            details=[
                ErrorDetail(
                    field="farm",
                    code="BLOCKING_RESOURCES",
                    message=resources,
                )
            ],
        )


class AuthenticationError(ApplicationError):
    status_code = 401
    code = "AUTH_INVALID_TOKEN"
    public_message = "Invalid authentication credentials"

    def __init__(self, detail: str, code: str = "AUTH_INVALID_TOKEN") -> None:
        if code in {"AUTH_TOKEN_MISSING", "AUTH_INVALID_TOKEN"}:
            self.code = code
        super().__init__(detail, headers={"WWW-Authenticate": "Bearer"})
        self.detail = {"code": self.code, "message": detail}

    @property
    def client_message(self) -> str:
        if self.code == "AUTH_TOKEN_MISSING":
            return "Authentication credentials are required"
        return self.public_message


class ServiceUnavailableError(ApplicationError):
    status_code = 503
    code = "SERVICE_UNAVAILABLE"
    public_message = "Service temporarily unavailable"


class RateLimitedError(ApplicationError):
    status_code = 429
    code = "RATE_LIMITED"
    public_message = "Too many requests"

    def __init__(self, retry_after: str) -> None:
        super().__init__(headers={"Retry-After": retry_after})


_HTTP_ERRORS: dict[int, tuple[str, str]] = {
    400: ("BAD_REQUEST", "Invalid request"),
    401: ("AUTH_INVALID_TOKEN", "Invalid authentication credentials"),
    403: ("AUTH_INSUFFICIENT_PERMISSIONS", "Insufficient permissions"),
    404: ("RESOURCE_NOT_FOUND", "Resource not found"),
    405: ("METHOD_NOT_ALLOWED", "Method not allowed"),
    409: ("CONFLICT", "The request conflicts with current state"),
    422: ("VALIDATION_ERROR", "Invalid request"),
    429: ("RATE_LIMITED", "Too many requests"),
    503: ("SERVICE_UNAVAILABLE", "Service temporarily unavailable"),
}

_VALIDATION_CODES = {
    "missing": "REQUIRED",
    "greater_than": "OUT_OF_RANGE",
    "greater_than_equal": "OUT_OF_RANGE",
    "less_than": "OUT_OF_RANGE",
    "less_than_equal": "OUT_OF_RANGE",
    "int_parsing": "INVALID_TYPE",
    "float_parsing": "INVALID_TYPE",
    "bool_parsing": "INVALID_TYPE",
    "string_type": "INVALID_TYPE",
    "list_type": "INVALID_TYPE",
    "dict_type": "INVALID_TYPE",
    "uuid_parsing": "INVALID_FORMAT",
    "date_from_datetime_parsing": "INVALID_FORMAT",
    "datetime_from_date_parsing": "INVALID_FORMAT",
    "extra_forbidden": "UNEXPECTED_FIELD",
}

_VALIDATION_MESSAGES = {
    "REQUIRED": "This field is required",
    "OUT_OF_RANGE": "Value is outside the allowed range",
    "INVALID_TYPE": "Value has an invalid type",
    "INVALID_FORMAT": "Value has an invalid format",
    "UNEXPECTED_FIELD": "Unexpected field",
    "INVALID_VALUE": "Value is invalid",
}


def _request_uuid() -> UUID:
    value = get_request_id()
    try:
        return UUID(value) if value else uuid4()
    except (TypeError, ValueError):
        return uuid4()


def _safe_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    safe: dict[str, str] = {}
    for name, value in (headers or {}).items():
        if name.lower() not in SAFE_PROTOCOL_HEADERS:
            continue
        clean = str(value)
        if "\r" in clean or "\n" in clean or len(clean) > 256:
            continue
        safe[name] = clean
    return safe


def _safe_field_path(location: Any) -> str:
    if not isinstance(location, (tuple, list)):
        return "request"
    parts: list[str] = []
    for part in location:
        if isinstance(part, int):
            parts.append(str(part))
        elif isinstance(part, str) and _SAFE_FIELD_PART.fullmatch(part):
            parts.append(part)
        else:
            parts.append("field")
    return ".".join(parts[:8]) or "request"


def _validation_details(exc: RequestValidationError) -> list[ErrorDetail]:
    details: list[ErrorDetail] = []
    for error in exc.errors()[:50]:
        error_type = str(error.get("type", ""))
        code = _VALIDATION_CODES.get(error_type, "INVALID_VALUE")
        details.append(
            ErrorDetail(
                field=_safe_field_path(error.get("loc")),
                code=code,
                message=_VALIDATION_MESSAGES[code],
            )
        )
    return details


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: list[ErrorDetail] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Build the only public error envelope used by the service."""

    set_request_error_code(code)
    request_id = _request_uuid()
    payload = ErrorResponse(
        error=ErrorEnvelope(
            code=code if _SAFE_CODE.fullmatch(code) else "INTERNAL_ERROR",
            message=message,
            request_id=request_id,
            details=details or [],
        )
    )
    response_headers = _safe_headers(headers)
    response_headers.setdefault("X-Request-ID", str(request_id))
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
        headers=response_headers,
    )


async def application_error_handler(
    request: Request, exc: ApplicationError
) -> JSONResponse:
    message = getattr(exc, "client_message", exc.public_message)
    return error_response(
        status_code=exc.status_code,
        code=exc.code,
        message=message,
        details=exc.details,
        headers=exc.headers,
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return error_response(
        status_code=422,
        code="VALIDATION_ERROR",
        message="Invalid request",
        details=_validation_details(exc),
    )


async def http_error_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    code, message = _HTTP_ERRORS.get(
        exc.status_code, ("HTTP_ERROR", "The request could not be completed")
    )
    if isinstance(exc.detail, Mapping):
        candidate = exc.detail.get("code")
        if isinstance(candidate, str) and _SAFE_CODE.fullmatch(candidate):
            # Only preserve a code when it agrees with the status family.
            allowed = {code}
            if exc.status_code == 401:
                allowed.update({"AUTH_TOKEN_MISSING", "AUTH_INVALID_TOKEN"})
            if candidate in allowed:
                code = candidate
    return error_response(
        status_code=exc.status_code,
        code=code,
        message=message,
        headers=exc.headers,
    )


async def dependency_error_handler(request: Request, exc: DBAPIError) -> JSONResponse:
    return error_response(
        status_code=503,
        code="SERVICE_UNAVAILABLE",
        message="Service temporarily unavailable",
    )


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return error_response(
        status_code=500,
        code="INTERNAL_ERROR",
        message="An internal error occurred",
    )


def install_exception_handlers(app: FastAPI) -> None:
    """Install normalization for domain, framework and unexpected failures."""

    app.add_exception_handler(ApplicationError, application_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(DBAPIError, dependency_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
