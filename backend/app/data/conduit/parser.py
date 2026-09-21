"""
Conduit fixture parser.

Deserializes the raw JSON payload from ``data/samples/weather.json`` into
typed ``ParsedObservation`` objects and a ``ParseReport`` summary.

The fixture root is an object with a ``data`` key that holds the record array.
A non-array ``data`` value (or a missing ``data`` key, or a non-object root) is
treated as a format error and raises ``ConduitFormatError``.

Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 3.1, 3.5
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Parser version — increment when parse logic changes.
PARSER_VERSION = "1.0.0"

# Plausible timestamp range for the fixture (Requirements 3.5).
_EARLIEST_TS = datetime(2020, 1, 1, tzinfo=timezone.utc)

# Numeric fields that appear in each fixture record.
# All of these are stored as float | None after parsing.
NUMERIC_FIELDS: tuple[str, ...] = (
    "rg1",
    "rg2",
    "rg1tt",
    "rg2tt",
    "rg1tp",
    "rg2tp",
    "temp_bmx",
    "press_bmx",
    "temp_mcp",
    "temp_sht",
    "humidity_sht",
    "si1145_vis",
    "si1145_ir",
    "si1145_uv",
    "wind_spd",
    "wind_dir",
    "wind_gust",
    "wind_gust_dir",
    "heat_idx",
    "wet_bulb_temp",
    "wet_bulb_globe_temp",
)


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------

class ConduitFormatError(Exception):
    """Raised when the root JSON structure cannot be used as a fixture payload.

    Requirements: 2.7
    """


@dataclass
class ParseError:
    """A single parse failure, either record-level or field-level."""

    record_index: int
    field: str | None  # None → record-level error
    raw_value: Any
    reason: str


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ParsedObservation:
    """One successfully-timestamped observation from the fixture.

    ``fields`` maps each known numeric field name to a parsed float or None.
    None means the value was absent, non-finite, or non-parseable as float.
    ``parse_errors`` carries the non-fatal field-level failures.

    Requirements: 2.1, 2.4, 2.5
    """

    raw_record: dict[str, Any]
    ts: datetime  # always UTC-aware, never None (records without ts are rejected)
    fields: dict[str, float | None]
    parse_errors: list[ParseError] = field(default_factory=list)


@dataclass
class ParseReport:
    """Summary of one ``parse_fixture`` call.

    Requirements: 2.6
    """

    accepted: int
    rejected: int        # records missing ts, unparseable ts, or out-of-range ts
    field_errors: int    # non-fatal field-level failures across all accepted records
    errors: list[ParseError] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_ts(raw: Any, index: int) -> datetime | ParseError:
    """Parse the ``ts`` field into a UTC-aware datetime.

    Returns a ``ParseError`` on failure so the caller can reject the record.

    Requirements: 2.2, 2.3, 3.1, 3.5
    """
    if raw is None:
        return ParseError(
            record_index=index,
            field="ts",
            raw_value=raw,
            reason="ts field is missing",
        )

    if not isinstance(raw, str):
        return ParseError(
            record_index=index,
            field="ts",
            raw_value=raw,
            reason=f"ts must be a string, got {type(raw).__name__}",
        )

    # Attempt ISO-8601 parse.
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return ParseError(
            record_index=index,
            field="ts",
            raw_value=raw,
            reason=f"ts value {raw!r} is not a valid ISO-8601 datetime",
        )

    # Normalise to UTC.
    dt_utc = dt.astimezone(timezone.utc)

    # Range check — Requirements 3.5.
    now_utc = datetime.now(timezone.utc)
    if dt_utc < _EARLIEST_TS:
        return ParseError(
            record_index=index,
            field="ts",
            raw_value=raw,
            reason=(
                f"ts {raw!r} is before the earliest plausible fixture date "
                f"({_EARLIEST_TS.isoformat()})"
            ),
        )
    if dt_utc > now_utc:
        return ParseError(
            record_index=index,
            field="ts",
            raw_value=raw,
            reason=(
                f"ts {raw!r} is in the future (after {now_utc.isoformat()})"
            ),
        )

    return dt_utc


def _parse_numeric(
    raw: Any,
    field_name: str,
    record_index: int,
) -> tuple[float | None, ParseError | None]:
    """Parse a single numeric field value.

    Returns ``(value, None)`` on success, ``(None, error)`` on failure.
    Non-finite values are treated as failures (Requirements 2.4).
    String values are coerced via float() (Requirements 2.5).
    """
    if raw is None:
        return None, ParseError(
            record_index=record_index,
            field=field_name,
            raw_value=raw,
            reason=f"field {field_name!r} is null",
        )

    # Already a float/int — check for non-finite.
    if isinstance(raw, (int, float)):
        f = float(raw)
        if not math.isfinite(f):
            return None, ParseError(
                record_index=record_index,
                field=field_name,
                raw_value=raw,
                reason=(
                    f"field {field_name!r} contains a non-finite value: {raw!r}"
                ),
            )
        return f, None

    # String → attempt float coercion.
    if isinstance(raw, str):
        try:
            f = float(raw)
        except ValueError:
            return None, ParseError(
                record_index=record_index,
                field=field_name,
                raw_value=raw,
                reason=(
                    f"field {field_name!r} string value {raw!r} cannot be "
                    "parsed as a float"
                ),
            )
        if not math.isfinite(f):
            return None, ParseError(
                record_index=record_index,
                field=field_name,
                raw_value=raw,
                reason=(
                    f"field {field_name!r} string value {raw!r} represents a "
                    "non-finite float"
                ),
            )
        return f, None

    # Any other type is unsupported.
    return None, ParseError(
        record_index=record_index,
        field=field_name,
        raw_value=raw,
        reason=(
            f"field {field_name!r} has unsupported type "
            f"{type(raw).__name__!r}"
        ),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_fixture(raw_json: str) -> tuple[list[ParsedObservation], ParseReport]:
    """Parse the raw Conduit fixture JSON string.

    The fixture envelope is ``{"status": ..., "data": [...records...]}``.
    A non-object root or a non-array ``data`` value raises ``ConduitFormatError``
    (Requirements 2.7).

    Per-record errors (missing/invalid ts, out-of-range ts) increment
    ``ParseReport.rejected`` and do not halt the run (Requirements 2.2, 2.3).

    Per-field errors (non-finite, non-parseable strings) are non-fatal: the
    field is set to None and the error is attached to the observation
    (Requirements 2.4, 2.5).

    Returns a list of ``ParsedObservation`` objects and a ``ParseReport``.
    """
    # --- Deserialise JSON ---
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ConduitFormatError(
            f"Fixture payload is not valid JSON: {exc}"
        ) from exc

    # --- Validate envelope structure (Requirements 2.7) ---
    if not isinstance(payload, dict):
        raise ConduitFormatError(
            f"Fixture root must be a JSON object, got {type(payload).__name__}"
        )

    if "data" not in payload:
        raise ConduitFormatError(
            "Fixture root object is missing the required 'data' key"
        )

    records = payload["data"]
    if not isinstance(records, list):
        raise ConduitFormatError(
            f"Fixture 'data' value must be a JSON array, "
            f"got {type(records).__name__}"
        )

    observations: list[ParsedObservation] = []
    all_errors: list[ParseError] = []
    rejected = 0
    total_field_errors = 0

    for index, record in enumerate(records):
        if not isinstance(record, dict):
            err = ParseError(
                record_index=index,
                field=None,
                raw_value=record,
                reason=f"record at index {index} is not a JSON object",
            )
            all_errors.append(err)
            rejected += 1
            continue

        # --- Parse timestamp (record-level rejection on failure) ---
        ts_raw = record.get("ts")
        ts_result = _parse_ts(ts_raw, index)
        if isinstance(ts_result, ParseError):
            all_errors.append(ts_result)
            rejected += 1
            continue

        ts: datetime = ts_result

        # --- Parse numeric fields (non-fatal) ---
        fields: dict[str, float | None] = {}
        record_field_errors: list[ParseError] = []

        for field_name in NUMERIC_FIELDS:
            raw_value = record.get(field_name)  # None if absent
            if field_name not in record:
                # Field absent → mark as None; no error stored here
                # (the QC engine will assign "missing" quality).
                fields[field_name] = None
            else:
                value, err = _parse_numeric(raw_value, field_name, index)
                fields[field_name] = value
                if err is not None:
                    record_field_errors.append(err)
                    all_errors.append(err)
                    total_field_errors += 1

        observations.append(
            ParsedObservation(
                raw_record=record,
                ts=ts,
                fields=fields,
                parse_errors=record_field_errors,
            )
        )

    report = ParseReport(
        accepted=len(observations),
        rejected=rejected,
        field_errors=total_field_errors,
        errors=all_errors,
    )

    return observations, report
