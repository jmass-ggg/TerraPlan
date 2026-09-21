"""
Risk Center API schemas.

Response models for /api/v1/farms/{farm_id}/risks routes.

Requirements: 8.2, 8.3, 8.4, 8.5
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# ActionRule response
# ---------------------------------------------------------------------------


class ActionRuleResponse(BaseModel):
    """
    Resolved action recommendation with completion overlay.

    Requirements: 7.2, 8.3
    """

    id: str = Field(description="Unique action identifier")
    priority: int = Field(description="Priority level (lower number = higher priority)")
    text: str = Field(description="Action recommendation text")
    source: str = Field(description="Advisory source (e.g., KALRO, FAO)")
    review_date: str = Field(description="Date this recommendation was last reviewed (YYYY-MM-DD)")
    completed: bool = Field(description="True when this action has been marked complete for this farm")
    completed_at: datetime | None = Field(
        description="UTC timestamp when the action was marked complete (null if not completed)"
    )


# ---------------------------------------------------------------------------
# HazardAssessment response
# ---------------------------------------------------------------------------


class HazardAssessmentResponse(BaseModel):
    """
    Single hazard risk assessment with resolved actions.

    Requirements: 1.4, 1.5, 8.2
    """

    hazard: str = Field(description="Hazard type: drought | heat | heavy_rainfall | flood_exposure | wind")
    index: int = Field(ge=0, le=100, description="Risk severity index (0=lowest, 100=highest)")
    level: str = Field(description="Risk level: Low | Medium | High | Unknown")
    driver: str = Field(description="Primary driver name (e.g., rainfall_deficit, temperature)")
    explanation: str = Field(description="Plain-language explanation of the assessment")
    horizon: str = Field(description="Assessment horizon: current | short_term | seasonal")
    at_risk_crops: list[str] = Field(description="List of crop names at risk (heat assessments only)")
    actions: list[ActionRuleResponse] = Field(description="Resolved action recommendations for this hazard")
    evidence_used: dict[str, str] = Field(
        description="Mapping of evidence field → status (real | demonstration | missing)"
    )
    engine_version: str = Field(description="Risk engine version identifier")
    snapshot_id: str | None = Field(description="Snapshot ID when source=snapshot, else null")
    data_mode: str = Field(description="Data mode: live | historical_replay | demonstration")


# ---------------------------------------------------------------------------
# RiskResponse
# ---------------------------------------------------------------------------


class RiskResponse(BaseModel):
    """
    Complete risk assessment response with all 5 hazard assessments.

    Requirements: 1.3, 8.2
    """

    farm_id: str = Field(description="Farm UUID")
    assessments: list[HazardAssessmentResponse] = Field(
        description="Exactly 5 hazard assessments (drought, heat, heavy_rainfall, flood_exposure, wind)"
    )
    engine_version: str = Field(description="Risk engine version identifier")
    snapshot_id: str | None = Field(description="Snapshot ID when available, else null")
    data_mode: str = Field(description="Data mode: live | historical_replay | demonstration")


# ---------------------------------------------------------------------------
# ActionCompletion response
# ---------------------------------------------------------------------------


class ActionCompletionResponse(BaseModel):
    """
    Action completion record response.

    Requirements: 7.4, 7.5, 8.5
    """

    farm_id: str = Field(description="Farm UUID")
    action_id: str = Field(description="Action recommendation ID")
    completed: bool = Field(description="Always true in this response")
    completed_at: datetime = Field(description="UTC timestamp when the action was marked complete")


# ---------------------------------------------------------------------------
# Timeline schemas
# ---------------------------------------------------------------------------


class TimelineHazardAssessment(BaseModel):
    """Single hazard assessment for a specific forecast date."""
    
    index: int | None = Field(
        ge=0, 
        le=100, 
        description="Risk severity index 0-100, or null if unavailable"
    )
    level: str = Field(description="Low | Medium | High | Unknown")


class RiskTimelinePoint(BaseModel):
    """Risk assessments for all hazards on a specific forecast date."""
    
    date: str = Field(description="Forecast date in YYYY-MM-DD format")
    drought: TimelineHazardAssessment
    heat: TimelineHazardAssessment
    heavy_rainfall: TimelineHazardAssessment
    flood_exposure: TimelineHazardAssessment
    wind: TimelineHazardAssessment


class RiskTimelineResponse(BaseModel):
    """7-day risk timeline with daily hazard assessments."""
    
    farm_id: str
    snapshot_id: str | None
    horizon_days: int = Field(description="Number of forecast days returned")
    generated_at: datetime = Field(description="Timeline generation timestamp (UTC)")
    points: list[RiskTimelinePoint] = Field(description="Daily risk assessments, ordered by date")

