"""
Action rules store for the Farm Risk Center.

Loads the versioned action rules YAML file at import time and exposes an
immutable in-memory tuple of ActionRule frozen dataclasses.

Requirements: 7.1, 7.2
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, field_validator, model_validator


# ---------------------------------------------------------------------------
# YAML location — relative to this file so it works regardless of cwd
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "actions"
_DEFAULT_RULES_PATH = _DATA_DIR / "rules_v1.yaml"

_VALID_HAZARDS = {"drought", "heat", "heavy_rainfall", "flood_exposure", "wind"}
_VALID_LEVELS = {"low", "medium", "high"}


# ---------------------------------------------------------------------------
# Pydantic validation model (used only during load)
# ---------------------------------------------------------------------------

class _RuleEntry(BaseModel):
    id: str
    hazard: str
    applicable_levels: list[str]
    priority: int
    text: str
    conflict_with: list[str]
    review_date: str
    source: str

    @field_validator("hazard")
    @classmethod
    def validate_hazard(cls, v: str) -> str:
        if v not in _VALID_HAZARDS:
            raise ValueError(
                f"hazard must be one of {_VALID_HAZARDS}, got {v!r}"
            )
        return v

    @field_validator("applicable_levels")
    @classmethod
    def validate_levels(cls, v: list[str]) -> list[str]:
        for level in v:
            if level not in _VALID_LEVELS:
                raise ValueError(
                    f"applicable_levels entries must be one of {_VALID_LEVELS}, got {level!r}"
                )
        if not v:
            raise ValueError("applicable_levels must contain at least one level")
        return v

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"priority must be >= 1, got {v}")
        return v

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("id must be a non-empty string")
        return v


class _RulesFile(BaseModel):
    version: str
    last_updated: str
    rules: list[_RuleEntry]

    @model_validator(mode="after")
    def validate_no_duplicate_ids(self) -> "_RulesFile":
        seen: set[str] = set()
        for rule in self.rules:
            if rule.id in seen:
                raise ValueError(f"Duplicate rule id {rule.id!r}")
            seen.add(rule.id)
        return self


# ---------------------------------------------------------------------------
# Domain dataclass (immutable, no Pydantic overhead at runtime)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ActionRule:
    """
    Immutable record for a single reviewed action recommendation.

    Requirements: 7.1, 7.2
    """

    id: str
    hazard: str                         # drought | heat | heavy_rainfall | flood_exposure | wind
    applicable_levels: tuple[str, ...]  # subset of {"low", "medium", "high"}
    priority: int                       # 1 = highest
    text: str                           # recommendation text (no specific quantities without source)
    conflict_with: tuple[str, ...]      # ids of rules that conflict with this one
    review_date: str                    # ISO date string, e.g. "2027-01-01"
    source: str                         # reviewed agronomic source


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_rules(path: Path | str | None = None) -> tuple[ActionRule, ...]:
    """
    Load and validate the YAML action rules file at *path*.

    Returns an immutable tuple of ActionRule dataclasses.

    Raises:
        ValueError: If the YAML fails schema validation, contains duplicate IDs,
                    or has conflict references to unknown rule IDs.
        FileNotFoundError: If the file does not exist.
    """
    resolved = Path(path) if path is not None else _DEFAULT_RULES_PATH

    with resolved.open("r", encoding="utf-8") as fh:
        raw: Any = yaml.safe_load(fh)

    try:
        rules_file = _RulesFile.model_validate(raw)
    except Exception as exc:
        raise ValueError(f"Action rules schema error in {resolved}: {exc}") from exc

    # Build id set for conflict reference validation
    all_ids = {entry.id for entry in rules_file.rules}

    for entry in rules_file.rules:
        for ref in entry.conflict_with:
            if ref not in all_ids:
                raise ValueError(
                    f"Rule {entry.id!r} references unknown conflict id {ref!r}"
                )

    return tuple(
        ActionRule(
            id=entry.id,
            hazard=entry.hazard,
            applicable_levels=tuple(entry.applicable_levels),
            priority=entry.priority,
            text=entry.text,
            conflict_with=tuple(entry.conflict_with),
            review_date=entry.review_date,
            source=entry.source,
        )
        for entry in rules_file.rules
    )


# ---------------------------------------------------------------------------
# Module-level singleton loaded at import time (Requirements: 7.1, 7.2)
# ---------------------------------------------------------------------------

ACTION_RULES: tuple[ActionRule, ...] = load_rules()
