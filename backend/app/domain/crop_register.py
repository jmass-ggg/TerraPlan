"""
Crop requirements register.

Requirements: 1.1, 1.2, 1.3, 1.4, 1.6

Loads the versioned crop requirements YAML file at import time and exposes
an immutable in-memory register of CropRequirements frozen dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# YAML location — relative to this file so it works regardless of cwd
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "crops"
_DEFAULT_REGISTER_PATH = _DATA_DIR / "requirements_v1.yaml"


# ---------------------------------------------------------------------------
# Pydantic validation model (used only during load)
# ---------------------------------------------------------------------------

class _TemperatureSpec(BaseModel):
    min_c: float
    optimum_c: float
    max_c: float

    @model_validator(mode="after")
    def validate_order(self) -> "_TemperatureSpec":
        if not (self.min_c <= self.optimum_c <= self.max_c):
            raise ValueError(
                f"Temperature values must satisfy min_c <= optimum_c <= max_c, "
                f"got {self.min_c} / {self.optimum_c} / {self.max_c}"
            )
        return self


class _RainfallSpec(BaseModel):
    min: float
    max: float

    @model_validator(mode="after")
    def validate_order(self) -> "_RainfallSpec":
        if self.min > self.max:
            raise ValueError(
                f"Rainfall min ({self.min}) must be <= max ({self.max})"
            )
        return self


class _SoilPhSpec(BaseModel):
    min: float
    max: float

    @model_validator(mode="after")
    def validate_order(self) -> "_SoilPhSpec":
        if self.min > self.max:
            raise ValueError(
                f"Soil pH min ({self.min}) must be <= max ({self.max})"
            )
        return self


class _RotationSpec(BaseModel):
    preferred_after: list[str] = Field(default_factory=list)
    avoid_after: list[str] = Field(default_factory=list)


class _CropEntry(BaseModel):
    name: str
    data_version: str
    category: str
    crop_type: str = "annual"
    family: str = "unknown"
    rotation: _RotationSpec = Field(default_factory=_RotationSpec)
    recovery_months: int = 0
    source_citation: str
    temperature: _TemperatureSpec
    rainfall_per_duration_mm: _RainfallSpec
    soil_ph: _SoilPhSpec
    soil_texture_preference: list[str]
    drought_tolerance: float
    heat_tolerance_ceiling_c: float
    duration_months: int

    @field_validator("drought_tolerance")
    @classmethod
    def validate_drought_tolerance(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"drought_tolerance must be in [0, 1], got {v}")
        return v

    @field_validator("duration_months")
    @classmethod
    def validate_duration(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"duration_months must be >= 1, got {v}")
        return v

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        allowed = {"cereal", "legume", "vegetable", "root", "fruit"}
        if v not in allowed:
            raise ValueError(f"category must be one of {allowed}, got {v!r}")
        return v

    @field_validator("crop_type")
    @classmethod
    def validate_crop_type(cls, v: str) -> str:
        if v not in {"annual", "perennial"}:
            raise ValueError(f"crop_type must be annual or perennial, got {v!r}")
        return v

    @field_validator("recovery_months")
    @classmethod
    def validate_recovery_months(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"recovery_months must be >= 0, got {v}")
        return v


class _RegisterFile(BaseModel):
    version: str
    last_updated: str
    crops: list[_CropEntry]


# ---------------------------------------------------------------------------
# Domain dataclass (immutable, no Pydantic overhead at runtime)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CropRequirements:
    """
    Immutable record of agronomic requirements for a single crop.

    All temperature values are in °C; rainfall in mm; soil_ph is dimensionless.
    drought_tolerance is a dimensionless score in [0, 1].
    """

    name: str
    data_version: str
    category: str                           # cereal | legume | vegetable | root | fruit
    source_citation: str

    # Temperature (°C)
    min_temp_c: float
    optimum_temp_c: float
    max_temp_c: float

    # Rainfall over full crop duration (mm)
    min_rainfall_mm: float
    max_rainfall_mm: float

    # Soil
    min_soil_ph: float
    max_soil_ph: float
    soil_texture_preferences: tuple[str, ...]   # immutable

    # Stress tolerance
    drought_tolerance: float                # 0 (none) – 1 (very high)
    heat_tolerance_ceiling_c: float         # lethal threshold

    # Duration
    duration_months: int

    # Annual planning
    crop_type: str = "annual"
    family: str = "unknown"
    preferred_after: tuple[str, ...] = ()
    avoid_after: tuple[str, ...] = ()
    recovery_months: int = 0


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RegisterMetadata:
    """Version and last_updated fields from the YAML register file header."""

    version: str
    last_updated: str


def load_register(
    path: Path | str | None = None,
) -> tuple[tuple[CropRequirements, ...], RegisterMetadata]:
    """
    Load and validate the YAML crop register at *path*.

    Returns a 2-tuple of (CropRequirements tuple, RegisterMetadata).

    Raises:
        ValueError: If the file cannot be parsed, fails schema validation,
                    or contains duplicate crop names.
        FileNotFoundError: If the file does not exist.
    """
    resolved = Path(path) if path is not None else _DEFAULT_REGISTER_PATH

    with resolved.open("r", encoding="utf-8") as fh:
        raw: Any = yaml.safe_load(fh)

    try:
        register_file = _RegisterFile.model_validate(raw)
    except Exception as exc:
        raise ValueError(f"Crop register schema error in {resolved}: {exc}") from exc

    # Duplicate name check (Requirement 1.1 — each crop appears once)
    seen_names: set[str] = set()
    for entry in register_file.crops:
        if entry.name in seen_names:
            raise ValueError(
                f"Duplicate crop name {entry.name!r} in crop register {resolved}"
            )
        seen_names.add(entry.name)

    crops = tuple(
        CropRequirements(
            name=entry.name,
            data_version=entry.data_version,
            category=entry.category,
            source_citation=entry.source_citation,
            min_temp_c=entry.temperature.min_c,
            optimum_temp_c=entry.temperature.optimum_c,
            max_temp_c=entry.temperature.max_c,
            min_rainfall_mm=entry.rainfall_per_duration_mm.min,
            max_rainfall_mm=entry.rainfall_per_duration_mm.max,
            min_soil_ph=entry.soil_ph.min,
            max_soil_ph=entry.soil_ph.max,
            soil_texture_preferences=tuple(entry.soil_texture_preference),
            drought_tolerance=entry.drought_tolerance,
            heat_tolerance_ceiling_c=entry.heat_tolerance_ceiling_c,
            duration_months=entry.duration_months,
            crop_type=entry.crop_type,
            family=entry.family,
            preferred_after=tuple(entry.rotation.preferred_after),
            avoid_after=tuple(entry.rotation.avoid_after),
            recovery_months=entry.recovery_months,
        )
        for entry in register_file.crops
    )
    metadata = RegisterMetadata(
        version=register_file.version,
        last_updated=register_file.last_updated,
    )
    return crops, metadata


# ---------------------------------------------------------------------------
# Module-level singletons loaded at import time (Requirement 1.4)
# ---------------------------------------------------------------------------

CROP_REGISTER: tuple[CropRequirements, ...]
REGISTER_METADATA: RegisterMetadata
CROP_REGISTER, REGISTER_METADATA = load_register()
