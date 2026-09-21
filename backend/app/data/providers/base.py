"""
Shared types for all environmental provider adapters.

Every adapter returns a ProviderResult containing:
- payload: JSON-serialisable dict for the snapshot (or None on failure)
- evidence_status: "accepted" | "unavailable" | "error"
- error_message: human-readable detail when not accepted
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Evidence status constants
# ---------------------------------------------------------------------------

EVIDENCE_ACCEPTED = "accepted"
EVIDENCE_UNAVAILABLE = "unavailable"
EVIDENCE_ERROR = "error"
EVIDENCE_INELIGIBLE = "ineligible"


# ---------------------------------------------------------------------------
# Provenance envelope helper
# ---------------------------------------------------------------------------

def provenance_envelope(
    value: float | None,
    unit: str,
    source: str,
    acquired_at: str,
    retrieved_at: str,
    data_mode: str = "live",
    quality: str = EVIDENCE_ACCEPTED,
    resolution_m: float | None = None,
    **extra,
) -> dict:
    """Build a standard provenance envelope for a single environmental value.

    Requirements: 7.3, 10.4
    """
    envelope: dict = {
        "value": value,
        "unit": unit,
        "source": source,
        "acquired_at": acquired_at,
        "retrieved_at": retrieved_at,
        "data_mode": data_mode,
        "quality": quality if value is not None else EVIDENCE_UNAVAILABLE,
        "resolution_m": resolution_m,
    }
    envelope.update(extra)
    return envelope


def null_envelope(unit: str, source: str, retrieved_at: str, data_mode: str = "live") -> dict:
    """Build a provenance envelope for an unavailable/null value.

    Requirements: 7.3, 10.5
    """
    return provenance_envelope(
        value=None,
        unit=unit,
        source=source,
        acquired_at=retrieved_at,
        retrieved_at=retrieved_at,
        data_mode=data_mode,
        quality=EVIDENCE_UNAVAILABLE,
    )


# ---------------------------------------------------------------------------
# ProviderResult
# ---------------------------------------------------------------------------

@dataclass
class ProviderResult:
    """Return type for every provider adapter fetch() function.

    Requirements: 2.4, 3.5, 4.6, 5.5, 6.5
    """

    payload: dict | None
    evidence_status: str  # "accepted" | "unavailable" | "error"
    error_message: str | None = field(default=None)

    @classmethod
    def unavailable(cls, message: str) -> "ProviderResult":
        return cls(payload=None, evidence_status=EVIDENCE_UNAVAILABLE, error_message=message)

    @classmethod
    def error(cls, message: str) -> "ProviderResult":
        return cls(payload=None, evidence_status=EVIDENCE_ERROR, error_message=message)
