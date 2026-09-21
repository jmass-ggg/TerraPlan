"""
Scenario Explorer API routes.

POST /api/v1/farms/{farm_id}/scenario  → compute and save a named scenario
GET  /api/v1/farms/{farm_id}/scenarios → list saved scenarios

Both routes require bearer authentication. A 404 is returned when the farm
does not exist or belongs to a different user.

Requirements: 2.5, 2.6
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import Field

from app.api.dependencies import get_current_principal, get_request_session
from app.api.schemas import ErrorResponse, ReadBaseSchema
from app.core.security import Principal
from app.services import scenario_service
from app.services.scenario_service import ScenarioDelta
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(tags=["Scenario Explorer"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class ScenarioDeltaRequest(ReadBaseSchema):
    """
    User-supplied bounded deltas for a scenario computation.

    Requirements: 2.1
    """

    rainfall_change_pct: float = Field(
        default=0.0,
        ge=-50.0,
        le=50.0,
        description="Rainfall percentage change (−50 to +50)",
    )
    temperature_change_c: float = Field(
        default=0.0,
        ge=-5.0,
        le=5.0,
        description="Temperature change in °C (−5 to +5)",
    )
    irrigation_mm_override: float | None = Field(
        default=None,
        ge=0.0,
        description="Optional irrigation override in mm (added on top of adjusted rainfall)",
    )


class ScenarioCreateRequest(ReadBaseSchema):
    """
    Request body for POST /farms/{farm_id}/scenario.

    Requirements: 2.5
    """

    name: str = Field(
        min_length=1,
        max_length=255,
        description="Human-readable name for this scenario",
    )
    delta: ScenarioDeltaRequest = Field(
        default_factory=ScenarioDeltaRequest,
        description="Climate delta parameters to apply",
    )


class CropScenarioResultResponse(ReadBaseSchema):
    """Baseline vs scenario suitability for one crop."""

    crop_name: str
    baseline_index: int
    scenario_index: int
    baseline_label: str
    scenario_label: str


class HazardScenarioResultResponse(ReadBaseSchema):
    """Baseline vs scenario hazard level for one hazard."""

    hazard: str
    baseline_level: str
    scenario_level: str
    baseline_index: int
    scenario_index: int


class ScenarioResponse(ReadBaseSchema):
    """
    Full scenario computation result.

    Requirements: 2.5, 3.4
    """

    id: str = Field(description="Scenario UUID")
    farm_id: str = Field(description="Farm UUID")
    name: str = Field(description="Scenario name")
    baseline_snapshot_id: str | None = Field(
        description="Snapshot UUID used as baseline, or null"
    )
    delta: dict[str, Any] = Field(
        description="Applied delta parameters (rainfall_change_pct, temperature_change_c, irrigation_mm_override)"
    )
    crops: list[CropScenarioResultResponse] = Field(
        description="Baseline vs scenario suitability for all 12 crops"
    )
    hazards: list[HazardScenarioResultResponse] = Field(
        description="Baseline vs scenario hazard levels for all 5 hazards"
    )
    engine_version: str = Field(description="Combined crop+risk engine version string")
    created_at: str = Field(description="ISO 8601 creation timestamp (UTC)")


class ScenarioSummaryResponse(ReadBaseSchema):
    """
    Summary of a saved scenario for list responses.

    Requirements: 2.6
    """

    id: str = Field(description="Scenario UUID")
    farm_id: str = Field(description="Farm UUID")
    name: str = Field(description="Scenario name")
    baseline_snapshot_id: str | None = Field(
        description="Snapshot UUID used as baseline, or null"
    )
    delta: dict[str, Any] = Field(description="Applied delta parameters")
    engine_version: str = Field(description="Combined crop+risk engine version string")
    created_at: str = Field(description="ISO 8601 creation timestamp (UTC)")


class ScenarioListResponse(ReadBaseSchema):
    """
    List of saved scenarios for a farm.

    Requirements: 2.6
    """

    farm_id: str = Field(description="Farm UUID")
    items: list[ScenarioSummaryResponse] = Field(description="Saved scenarios (newest first)")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/farms/{farm_id}/scenario",
    response_model=ScenarioResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid bearer token"},
        404: {"model": ErrorResponse, "description": "Farm not found or owned by a different user"},
        422: {"model": ErrorResponse, "description": "Invalid delta parameters"},
    },
    summary="Compute and save a scenario",
    description=(
        "Applies the supplied climate delta to the farm's latest analysis snapshot, "
        "recomputes crop suitability and hazard levels, and saves the result as an "
        "immutable named scenario. Returns 404 when the farm has no analysis snapshot. "
        "Requirements: 2.2, 2.5, 3.1, 3.2, 3.3, 3.4"
    ),
)
async def create_scenario(
    farm_id: UUID,
    data: ScenarioCreateRequest,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_request_session),
) -> ScenarioResponse:
    """
    POST /api/v1/farms/{farm_id}/scenario

    Requirements: 2.5, 2.2, 3.4
    """
    delta = ScenarioDelta(
        rainfall_change_pct=data.delta.rainfall_change_pct,
        temperature_change_c=data.delta.temperature_change_c,
        irrigation_mm_override=data.delta.irrigation_mm_override,
    )

    result = await scenario_service.compute_scenario(
        session=session,
        principal=principal,
        farm_id=farm_id,
        delta=delta,
        name=data.name,
    )

    return ScenarioResponse(
        id=result.id,
        farm_id=result.farm_id,
        name=result.name,
        baseline_snapshot_id=result.baseline_snapshot_id,
        delta=result.delta,
        crops=[
            CropScenarioResultResponse(
                crop_name=c.crop_name,
                baseline_index=c.baseline_index,
                scenario_index=c.scenario_index,
                baseline_label=c.baseline_label,
                scenario_label=c.scenario_label,
            )
            for c in result.crops
        ],
        hazards=[
            HazardScenarioResultResponse(
                hazard=h.hazard,
                baseline_level=h.baseline_level,
                scenario_level=h.scenario_level,
                baseline_index=h.baseline_index,
                scenario_index=h.scenario_index,
            )
            for h in result.hazards
        ],
        engine_version=result.engine_version,
        created_at=result.created_at,
    )


@router.get(
    "/farms/{farm_id}/scenarios",
    response_model=ScenarioListResponse,
    status_code=status.HTTP_200_OK,
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid bearer token"},
        404: {"model": ErrorResponse, "description": "Farm not found or owned by a different user"},
    },
    summary="List saved scenarios",
    description=(
        "Returns all saved scenarios for the specified farm, newest first. "
        "Only scenarios belonging to the authenticated user's farm are returned. "
        "Requirements: 2.6, 2.7"
    ),
)
async def list_scenarios(
    farm_id: UUID,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_request_session),
) -> ScenarioListResponse:
    """
    GET /api/v1/farms/{farm_id}/scenarios

    Requirements: 2.6
    """
    summaries = await scenario_service.list_scenarios(
        session=session,
        principal=principal,
        farm_id=farm_id,
    )

    return ScenarioListResponse(
        farm_id=str(farm_id),
        items=[
            ScenarioSummaryResponse(
                id=s.id,
                farm_id=s.farm_id,
                name=s.name,
                baseline_snapshot_id=s.baseline_snapshot_id,
                delta=s.delta,
                engine_version=s.engine_version,
                created_at=s.created_at,
            )
            for s in summaries
        ],
    )
