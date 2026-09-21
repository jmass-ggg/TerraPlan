"""Privacy-preserving structured logging configuration."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from .config import Settings


_EVENT = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_METHOD = re.compile(r"^[A-Z]{1,12}$")
_ROUTE = re.compile(r"^(?:<unmatched>|/[A-Za-z0-9_{}./:-]{0,255})$")

_ALLOWED_EXTRA = frozenset(
    {
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
)

_STANDARD_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__) | {
    "message",
    "asctime",
}


def _safe_event(value: Any) -> str:
    value = str(value or "application.event")
    return value if _EVENT.fullmatch(value) else "application.event"


class PrivacyFilter(logging.Filter):
    """Strip arbitrary extras, messages and traceback values from every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.event = _safe_event(getattr(record, "event", None))
        if not getattr(record, "request_id", None):
            # Local import avoids a module cycle during application startup.
            from .request_context import get_request_id

            context_request_id = get_request_id()
            if context_request_id:
                record.request_id = context_request_id
        # Raw messages and arguments are an uncontrolled exfiltration channel.
        record.msg = record.event
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None

        for key in tuple(record.__dict__):
            if key not in _STANDARD_RECORD_FIELDS and key not in _ALLOWED_EXTRA:
                del record.__dict__[key]
        return True


class PrivacyJSONFormatter(logging.Formatter):
    """Serialize only the design-approved diagnostic field allowlist."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "severity": record.levelname,
            "event": _safe_event(getattr(record, "event", None)),
        }
        self._copy_safe_fields(record, payload)
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _copy_safe_fields(record: logging.LogRecord, payload: dict[str, Any]) -> None:
        error_code = getattr(record, "error_code", None)
        if isinstance(error_code, str) and _CODE.fullmatch(error_code):
            payload["error_code"] = error_code

        request_id = getattr(record, "request_id", None)
        if isinstance(request_id, str) and len(request_id) == 36:
            payload["request_id"] = request_id

        method = getattr(record, "method", None)
        if isinstance(method, str) and _METHOD.fullmatch(method):
            payload["method"] = method

        route = getattr(record, "route", None)
        if isinstance(route, str) and _ROUTE.fullmatch(route):
            payload["route"] = route

        status = getattr(record, "status", None)
        if isinstance(status, int) and 100 <= status <= 599:
            payload["status"] = status

        duration = getattr(record, "duration_ms", None)
        if isinstance(duration, (int, float)) and duration >= 0:
            payload["duration_ms"] = round(float(duration), 3)

        for name in ("environment", "auth_mode", "data_mode", "outcome"):
            value = getattr(record, name, None)
            if isinstance(value, str) and re.fullmatch(r"[a-z_]{1,32}", value):
                payload[name] = value

        exception_type = getattr(record, "exception_type", None)
        if isinstance(exception_type, str) and re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_]{0,63}", exception_type
        ):
            payload["exception_type"] = exception_type


class PrivacyTextFormatter(PrivacyJSONFormatter):
    """Compact development formatter with the same field restrictions."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "severity": record.levelname,
            "event": _safe_event(getattr(record, "event", None)),
        }
        self._copy_safe_fields(record, payload)
        return " ".join(f"{key}={value}" for key, value in payload.items())


def configure_logging(settings: Settings) -> logging.Handler:
    """Configure application and secondary loggers without unsafe fallbacks."""

    root = logging.getLogger()
    level = getattr(logging, settings.log_level)
    root.setLevel(level)

    privacy_filter = PrivacyFilter()
    formatter: logging.Formatter
    if settings.environment.value == "production":
        formatter = PrivacyJSONFormatter()
    else:
        formatter = PrivacyTextFormatter()

    for existing in root.handlers:
        existing.addFilter(privacy_filter)

    handler = next(
        (h for h in root.handlers if getattr(h, "_farmtwin_handler", False)),
        None,
    )
    if handler is None:
        handler = logging.StreamHandler()
        handler._farmtwin_handler = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    handler.setLevel(level)
    handler.setFormatter(formatter)
    handler.addFilter(privacy_filter)

    # These libraries commonly emit URLs, headers, SQL, parameters or request
    # targets.  Let records propagate through the privacy filter, and suppress
    # verbose access/driver output at its source.
    for name in (
        "uvicorn",
        "uvicorn.error",
        "fastapi",
        "starlette",
        "httpx",
        "httpcore",
        "asyncpg",
        "sqlalchemy",
        "sqlalchemy.engine",
        "sqlalchemy.pool",
    ):
        secondary = logging.getLogger(name)
        secondary.handlers.clear()
        secondary.propagate = True
        secondary.setLevel(max(level, logging.WARNING))
    logging.getLogger("uvicorn.access").disabled = True
    return handler


def request_log_fields(settings: Settings, **values: Any) -> dict[str, Any]:
    """Create allowlisted logging extras with deployment context."""

    fields = {
        "environment": settings.environment.value,
        "auth_mode": settings.auth.mode.value,
        "data_mode": settings.data_mode.value,
    }
    fields.update({key: value for key, value in values.items() if key in _ALLOWED_EXTRA})
    return fields
