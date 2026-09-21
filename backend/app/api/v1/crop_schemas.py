"""
Pydantic schemas for the Crop Simulator API.

Requirements: 6.3, 6.6
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from app.api.schemas import BaseSchema, ReadBaseSchema


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class SimulateRequest(BaseSchema):
    """
    Request body for POST /api/v1/farms/{farm_id}/simulate-crop.

    Requirements: 6.1, 6.3
    """

    model_config = ConfigDict(extra="forbid")

    crop_name: str | None = Field(
        default=None,
        description=(
            "Crop to evaluate. When omitted all crops are ranked and "
            "a CropRankingResponse is returned instead of a SimulationResponse."
        ),
    )
    planting_date: date = Field(description="Assumed planting date (ISO 8601 date)")
    cultivation_mode: Literal["rain_fed", "irrigated"] = Field(
        default="rain_fed",
        description="Cultivation mode: rain_fed or irrigated",
    )
    irrigation_mm: float | None = Field(
        default=None,
        ge=0,
        description="Monthly supplemental irrigation (mm). Required when cultivation_mode='irrigated'.",
    )

    @model_validator(mode="after")
    def require_irrigation_when_irrigated(self) -> "SimulateRequest":
        if self.cultivation_mode == "irrigated" and self.irrigation_mm is None:
            raise ValueError(
                "irrigation_mm is required when cultivation_mode is 'irrigated'"
            )
        return self


# ---------------------------------------------------------------------------
# Response fragments
# ---------------------------------------------------------------------------


class ComponentScoresResponse(ReadBaseSchema):
    """
    Per-component 0–100 scores from the crop suitability engine.

    Requirements: 6.6
    """

    temperature: int | None = Field(description="Temperature match score (0–100)")
    water: int | None = Field(description="Water / rainfall adequacy score (0–100)")
    soil: int | None = Field(description="Soil compatibility score (0–100)")
    heat_safety: int | None = Field(description="Heat safety score (0–100)")
    drought_flood_safety: int | None = Field(description="Drought / flood safety score (0–100)")
    environmental_condition: int | None = Field(description="Environmental condition score (0–100)")


class SimulationResultResponse(ReadBaseSchema):
    """
    Full result for a single crop simulation.

    Requirements: 3.7, 6.3
    """

    crop_name: str = Field(description="Crop name")
    suitability_index: int | None = Field(description="Overall suitability index (0–100)", ge=0, le=100)
    label: str = Field(
        description="Qualitative label: 'Good match' | 'Possible match' | 'Higher caution'"
    )
    components: ComponentScoresResponse = Field(description="Per-component scores")
    limiting_factor: str = Field(description="Component that most constrained the score")
    reason: str = Field(description="Plain-language explanation of the score")
    hard_exclusion: bool = Field(
        description="True when a lethal threshold is exceeded (suitability_index == 0)"
    )
    hard_exclusion_reason: str | None = Field(
        default=None, description="Human-readable reason for hard exclusion, or null"
    )
    engine_version: str = Field(description="Engine identifier (e.g. 'farmtwin-crop-v1')")
    snapshot_id: str | None = Field(
        default=None,
        description="Snapshot UUID used as evidence basis, or null for demonstration fallback",
    )
    data_mode: str = Field(
        description="Evidence mode: 'live' | 'historical_replay' | 'demonstration'"
    )
    input_completeness: dict[str, str] = Field(
        description="Per-field completeness: 'real' | 'demonstration' | 'missing'"
    )


class SimulationResponse(ReadBaseSchema):
    """
    Response for POST simulate-crop when a specific crop_name was supplied.

    Requirements: 6.3
    """

    farm_id: str = Field(description="Farm UUID")
    selected: SimulationResultResponse = Field(description="Score for the requested crop")
    alternatives: list[SimulationResultResponse] = Field(
        description="Top 3 alternative crops scored under identical inputs"
    )
    engine_version: str = Field(description="Engine identifier")
    snapshot_id: str | None = Field(
        default=None, description="Snapshot UUID or null"
    )
    data_mode: str = Field(description="Evidence mode")


class CropRankingResponse(ReadBaseSchema):
    """
    Response for POST simulate-crop when no crop_name is supplied.

    Requirements: 4.1, 6.3
    """

    farm_id: str = Field(description="Farm UUID")
    ranked: list[SimulationResultResponse] = Field(
        description="All 12 crops ranked by suitability_index descending"
    )
    engine_version: str = Field(description="Engine identifier")
    snapshot_id: str | None = Field(
        default=None, description="Snapshot UUID or null"
    )
    data_mode: str = Field(description="Evidence mode")


class CropEntry(ReadBaseSchema):
    """
    Summary entry for GET /api/v1/crops.

    Requirements: 1.6, 6.2
    """

    name: str = Field(description="Crop name")
    category: str = Field(description="Crop category: cereal | legume | vegetable | root")
    data_version: str = Field(description="Register entry version identifier")
    last_updated: str = Field(description="Register last_updated date (ISO 8601)")


class CropListResponse(ReadBaseSchema):
    """
    Response for GET /api/v1/crops.

    Requirements: 1.6, 6.2
    """

    crops: list[CropEntry] = Field(description="All crops in the register")
    register_version: str = Field(description="Register version string")
    last_updated: str = Field(description="Register last_updated date (ISO 8601)")


# ---------------------------------------------------------------------------
# AI Explanation Response
# ---------------------------------------------------------------------------


class CropExplanationFactorResponse(ReadBaseSchema):
    """A single strength or concern about the crop."""

    factor: str = Field(description="Component name (e.g., 'Temperature', 'Water')")
    message: str = Field(description="Explanation of why this is a strength or concern")


class CropExplanationResponse(ReadBaseSchema):
    """Farmer-friendly AI-generated explanation of crop suitability."""

    headline: str = Field(description="Short headline summarizing the assessment")
    summary: str = Field(description="Brief summary of the crop's suitability")
    strengths: list[CropExplanationFactorResponse] = Field(
        description="Positive factors (max 2)"
    )
    concerns: list[CropExplanationFactorResponse] = Field(
        description="Limiting factors (max 2)"
    )
    action: str | None = Field(
        default=None,
        description="Actionable suggestion for the farmer (optional)",
    )
    data_note: str | None = Field(
        default=None,
        description="Note about missing or incomplete data (optional)",
    )
    source: str = Field(
        description="Explanation source: 'ai' or 'deterministic_fallback'"
    )
    cached: bool = Field(description="Whether this explanation was retrieved from cache")


class CropExplanationFullResponse(ReadBaseSchema):
    """Full response including crop score and AI explanation."""

    crop_name: str = Field(description="Crop name")
    suitability_index: int | None = Field(
        description="Overall suitability score (0-100)", ge=0, le=100
    )
    label: str = Field(description="Suitability label")
    explanation: CropExplanationResponse = Field(
        description="AI-generated farmer-friendly explanation"
    )

