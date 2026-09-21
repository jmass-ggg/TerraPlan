"""
Pydantic schemas for the Annual Planner API.

Requirements: 2.1
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.api.schemas import BaseSchema, ReadBaseSchema


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class PlanEntryCreate(BaseSchema):
    """
    Request body for POST /api/v1/farms/{farm_id}/crop-plan/entries.

    Requirements: 2.1
    """

    model_config = ConfigDict(extra="forbid")

    field_name: str | None = Field(default=None, max_length=100)
    crop_name: str = Field(description="Crop name from the Phase 6 register")
    planting_date: date = Field(description="Intended planting date (ISO 8601 date)")
    cultivation_mode: Literal["rain_fed", "irrigated"] = Field(
        default="rain_fed",
        description="Cultivation mode: rain_fed or irrigated",
    )
    irrigation_mm: float | None = Field(
        default=None,
        ge=0,
        description="Monthly supplemental irrigation (mm). Required when cultivation_mode='irrigated'.",
    )
    area_ha: float = Field(
        gt=0,
        description="Allocated area for this entry in hectares",
    )

    @model_validator(mode="after")
    def require_irrigation_when_irrigated(self) -> "PlanEntryCreate":
        if self.cultivation_mode == "irrigated" and self.irrigation_mm is None:
            raise ValueError(
                "irrigation_mm is required when cultivation_mode is 'irrigated'"
            )
        return self


class PlanEntryUpdate(BaseSchema):
    """
    Request body for PATCH /api/v1/farms/{farm_id}/crop-plan/entries/{entry_id}.

    All fields are optional; only supplied fields are updated.

    Requirements: 2.5
    """

    model_config = ConfigDict(extra="forbid")

    expected_revision: int | None = Field(default=None, ge=1)
    field_name: str | None = Field(default=None, max_length=100)
    planting_date: date | None = Field(
        default=None,
        description="New planting date (triggers harvest_date re-derivation)",
    )
    cultivation_mode: Literal["rain_fed", "irrigated"] | None = Field(
        default=None,
        description="Updated cultivation mode",
    )
    irrigation_mm: float | None = Field(
        default=None,
        ge=0,
        description="Updated monthly irrigation (mm)",
    )
    area_ha: float | None = Field(
        default=None,
        gt=0,
        description="Updated allocated area in hectares",
    )


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class PlanEntryResponse(ReadBaseSchema):
    """
    Response for a single saved Plan_Entry.

    Requirements: 2.1, 2.4
    """

    revision: int = 1
    field_name: str | None = None
    id: UUID = Field(description="Entry UUID")
    farm_id: UUID = Field(description="Farm UUID")
    crop_name: str = Field(description="Crop name")
    planting_date: date = Field(description="Planned planting date")
    harvest_date: date = Field(description="Derived harvest date")
    cultivation_mode: str = Field(description="rain_fed or irrigated")
    irrigation_mm: float | None = Field(
        default=None,
        description="Monthly irrigation (mm) when irrigated, otherwise null",
    )
    area_ha: float = Field(description="Allocated area in hectares")
    snapshot_id: UUID | None = Field(
        default=None,
        description="Snapshot UUID used at save time, or null for demonstration mode",
    )
    data_mode: str = Field(description="Data mode at save time: live | historical_replay | demonstration")
    suitability_index: int | None = Field(
        description="Crop suitability score (0–100) from the engine at save time",
        ge=0,
        le=100,
    )
    engine_version: str = Field(description="Crop engine version identifier")
    created_at: datetime = Field(description="UTC timestamp when this entry was created")
    updated_at: datetime | None = Field(
        default=None,
        description="UTC timestamp of last update, or null",
    )


class MonthRecommendationResponse(ReadBaseSchema):
    """
    Top-3 crop recommendations for a single calendar month.

    Requirements: 1.3, 1.4
    """

    month: int = Field(description="Calendar month (1–12)", ge=1, le=12)
    month_name: str = Field(description="Full month name (e.g. 'January')")
    data_mode: str = Field(description="Data mode: live | historical_replay | demonstration")
    snapshot_id: str | None = Field(
        default=None,
        description="Snapshot UUID used for this month's recommendations, or null",
    )
    recommendations: list[dict] = Field(
        description="Top 3 crops: crop_name, suitability_index, label, limiting_factor"
    )


class ChangeProposalResponse(ReadBaseSchema):
    """
    Response for a pending Change_Proposal.

    Requirements: 4.2
    """

    id: UUID = Field(description="Proposal UUID")
    farm_id: UUID = Field(description="Farm UUID")
    entry_id: UUID = Field(description="Plan_Entry UUID this proposal refers to")
    old_suitability_index: int | None = Field(
        description="Suitability index at the time the entry was saved",
        ge=0,
        le=100,
    )
    new_suitability_index: int | None = Field(
        description="Suitability index under the new snapshot",
        ge=0,
        le=100,
    )
    changed_inputs: dict = Field(
        description="Environmental inputs that changed: {field: {old: ..., new: ...}}"
    )
    new_snapshot_id: UUID = Field(description="Snapshot that triggered this proposal")
    issue_date: datetime = Field(description="UTC timestamp when this proposal was generated")
    status: str = Field(description="pending | accepted | dismissed")
    created_at: datetime = Field(description="UTC timestamp when this proposal was created")


class TimelineItemResponse(ReadBaseSchema):
    month: int = Field(ge=1, le=12)
    month_name: str
    crop_name: str | None
    stage: str
    action: str
    season_id: str | None
    suitability_index: int | None = Field(default=None, ge=0, le=100)
    planning_score: int | None = Field(default=None, ge=0, le=100)
    plant_month: int | None = Field(default=None, ge=1, le=12)
    harvest_month: int | None = Field(default=None, ge=1, le=12)
    duration_months: int | None = Field(default=None, ge=1)
    previous_crop: str | None = None
    rotation_effect: str | None = None
    reason: str | None = None
    limiting_factor: str | None = None
    continues_next_year: bool = False
    data_mode: str
    snapshot_id: str | None = None
    saved: bool = False


class PerennialOpportunityResponse(ReadBaseSchema):
    crop_name: str
    suitability_index: int = Field(ge=0, le=100)
    label: str
    limiting_factor: str
    reason: str


class AnnualPlanResponse(ReadBaseSchema):
    """
    Response for GET /api/v1/farms/{farm_id}/crop-plan.

    Returns the 12-month recommendation grid, all saved entries for the
    requested year, and any pending Change_Proposals.

    Requirements: 1.3, 2.4
    """

    farm_id: str = Field(description="Farm UUID")
    year: int = Field(description="Calendar year for the plan")
    months: list[MonthRecommendationResponse] = Field(
        description="12 monthly recommendation records (one per calendar month)"
    )
    timeline: list[TimelineItemResponse] = Field(
        default_factory=list,
        description="Sequential occupancy-aware field activity for the year"
    )
    perennial_opportunities: list[PerennialOpportunityResponse] = Field(
        default_factory=list,
        description="Long-term crops excluded from the annual rotation"
    )
    entries: list[PlanEntryResponse] = Field(
        description="Saved Plan_Entries for this farm and year"
    )
    proposals: list[ChangeProposalResponse] = Field(
        description="Pending Change_Proposals for this farm"
    )
    solver_status: str = Field(default="not_run", description="Internal plan-generation status")
    fallback_used: bool = Field(default=False, description="Whether deterministic fallback generated the plan")
    explanation: str = Field(
        default="FarmTwin balanced crop conditions, growing time, your calendar and crop rotation.",
        description="Short deterministic farmer explanation",
    )


class ExportEntryRow(ReadBaseSchema):
    """A single row in the CSV/JSON plan export."""

    crop_name: str
    planting_date: str
    harvest_date: str
    cultivation_mode: str
    irrigation_mm: float | None
    area_ha: float
    suitability_index: int | None
    data_mode: str


class PlanExportResponse(ReadBaseSchema):
    """
    Response for GET /api/v1/farms/{farm_id}/crop-plan/export.

    Requirements: 5.6
    """

    farm_id: str = Field(description="Farm UUID")
    farm_name: str = Field(description="Farm name")
    year: int = Field(description="Calendar year")
    data_mode: str = Field(description="Dominant data mode across entries")
    entries: list[ExportEntryRow] = Field(description="All plan entries for the year")
    created_at: datetime = Field(description="UTC timestamp of export generation")
