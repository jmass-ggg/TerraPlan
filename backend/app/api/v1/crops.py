"""
Crop Simulator API routes.

GET  /api/v1/crops                              → 200 CropListResponse
POST /api/v1/farms/{farm_id}/simulate-crop      → 200 SimulationResponse | CropRankingResponse

Both routes require bearer authentication via resolve_principal.
Ownership-scoped 404 on wrong owner; 422 on validation failures.

Requirements: 6.1, 6.2, 6.4, 6.5
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    get_current_principal,
    get_request_session,
)
from app.api.schemas import ErrorResponse
from app.api.v1.crop_schemas import (
    CropEntry,
    CropExplanationFactorResponse,
    CropExplanationFullResponse,
    CropExplanationResponse,
    CropListResponse,
    CropRankingResponse,
    SimulateRequest,
    SimulationResponse,
)
from app.core.security import Principal
from app.domain.crop_register import CROP_REGISTER, REGISTER_METADATA
from app.services import crop_service
from app.services.ai import get_crop_explanation_provider

router = APIRouter(tags=["Crop Simulator"])


@router.get(
    "/crops",
    response_model=CropListResponse,
    summary="List all supported crops",
    description=(
        "Returns the complete crop register with name, category, data_version, "
        "and the register's last_updated date. Requirements: 1.6, 6.2"
    ),
)
async def list_crops(
    principal: Principal = Depends(get_current_principal),
) -> CropListResponse:
    """
    Return all crops in the register.

    The crop list is derived from the in-memory CROP_REGISTER loaded at startup.
    Authentication is required for API surface consistency (Requirement 6.5).

    Requirements: 1.6, 6.2
    """
    crops = [
        CropEntry(
            name=crop.name,
            category=crop.category,
            data_version=crop.data_version,
            last_updated=REGISTER_METADATA.last_updated,
        )
        for crop in CROP_REGISTER
    ]
    return CropListResponse(
        crops=crops,
        register_version=REGISTER_METADATA.version,
        last_updated=REGISTER_METADATA.last_updated,
    )


@router.post(
    "/farms/{farm_id}/simulate-crop",
    response_model=SimulationResponse | CropRankingResponse,
    status_code=status.HTTP_200_OK,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Farm not found or not owned by authenticated user",
        },
        422: {
            "model": ErrorResponse,
            "description": (
                "Invalid request: unknown crop name, missing irrigation_mm "
                "when irrigated, or required inputs absent with no fallback"
            ),
        },
    },
    summary="Simulate crop suitability",
    description=(
        "Score a specific crop (SimulationResponse + 3 alternatives) or rank all "
        "supported crops (CropRankingResponse) for the given farm, planting date, "
        "and cultivation mode. Uses the latest Phase 5 snapshot when available; "
        "falls back to the demonstration climate profile otherwise. "
        "Returns 404 for missing or cross-user farms. "
        "Requirements: 6.1, 6.4, 6.5"
    ),
)
async def simulate_crop(
    farm_id: UUID,
    body: SimulateRequest,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_request_session),
) -> SimulationResponse | CropRankingResponse:
    """
    POST /api/v1/farms/{farm_id}/simulate-crop

    Delegates to crop_service.simulate() which:
    1. Fetches the farm with ownership check (NotFoundError → 404).
    2. Loads the latest AnalysisSnapshot or falls back to demonstration profile.
    3. Scores the requested crop or ranks all 12 crops.

    Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 4.1, 4.2, 6.1, 6.4, 6.5
    """
    return await crop_service.simulate(
        session=session,
        principal=principal,
        farm_id=farm_id,
        crop_name=body.crop_name,
        planting_date=body.planting_date,
        cultivation_mode=body.cultivation_mode,
        irrigation_mm=body.irrigation_mm,
    )


@router.post(
    "/farms/{farm_id}/crops/{crop_name}/explanation",
    response_model=CropExplanationFullResponse,
    status_code=status.HTTP_200_OK,
    responses={
        404: {
            "model": ErrorResponse,
            "description": "Farm or crop not found",
        },
        422: {
            "model": ErrorResponse,
            "description": "Invalid request parameters",
        },
    },
    summary="Get AI explanation for crop suitability",
    description=(
        "Generate a farmer-friendly explanation of why a specific crop received its "
        "suitability score. Uses AI when available, falls back to deterministic "
        "explanation otherwise. Results are cached to avoid redundant AI calls."
    ),
)
async def get_crop_explanation(
    farm_id: UUID,
    crop_name: str,
    body: SimulateRequest,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_request_session),
) -> CropExplanationFullResponse:
    """
    POST /api/v1/farms/{farm_id}/crops/{crop_name}/explanation

    Generates an AI-powered explanation for a specific crop's suitability score.
    The crop is first scored using the deterministic engine, then the AI explains
    the result in farmer-friendly language.
    """
    # First, get the crop simulation result
    simulation_response = await crop_service.simulate(
        session=session,
        principal=principal,
        farm_id=farm_id,
        crop_name=crop_name,
        planting_date=body.planting_date,
        cultivation_mode=body.cultivation_mode,
        irrigation_mm=body.irrigation_mm,
    )

    # Extract the selected crop result
    if not isinstance(simulation_response, SimulationResponse):
        # Should not happen if crop_name is provided, but handle gracefully
        from app.core.exceptions import FarmValidationError
        raise FarmValidationError(
            field="crop_name",
            detail_code="INVALID_VALUE",
            message=f"Crop {crop_name} not found",
        )

    result_response = simulation_response.selected

    # Find the crop requirements
    crop_req = next(
        (c for c in CROP_REGISTER if c.name.lower() == crop_name.lower()),
        None,
    )
    if crop_req is None:
        from app.core.exceptions import FarmValidationError
        raise FarmValidationError(
            field="crop_name",
            detail_code="INVALID_VALUE",
            message=f"Crop {crop_name} not found in register",
        )

    # Convert response back to SimulationResult for the AI service
    from app.domain.crop_engine import ComponentScores, SimulationResult

    components = ComponentScores(
        temperature=float(result_response.components.temperature) if result_response.components.temperature is not None else None,
        water=float(result_response.components.water) if result_response.components.water is not None else None,
        soil=float(result_response.components.soil) if result_response.components.soil is not None else None,
        heat_safety=float(result_response.components.heat_safety) if result_response.components.heat_safety is not None else None,
        drought_flood_safety=float(result_response.components.drought_flood_safety) if result_response.components.drought_flood_safety is not None else None,
        environmental_condition=float(result_response.components.environmental_condition) if result_response.components.environmental_condition is not None else None,
    )

    simulation_result = SimulationResult(
        crop_name=result_response.crop_name,
        suitability_index=result_response.suitability_index,
        label=result_response.label,
        components=components,
        limiting_factor=result_response.limiting_factor,
        reason=result_response.reason,
        hard_exclusion=result_response.hard_exclusion,
        hard_exclusion_reason=result_response.hard_exclusion_reason,
        engine_version=result_response.engine_version,
        snapshot_id=result_response.snapshot_id,
        data_mode=result_response.data_mode,
        input_completeness=result_response.input_completeness,
    )

    # Generate AI explanation
    provider = get_crop_explanation_provider()
    explanation = await provider.generate(
        result=simulation_result,
        crop_requirements=crop_req,
        farm_id=str(farm_id),
    )

    # Convert to response format
    return CropExplanationFullResponse(
        crop_name=result_response.crop_name,
        suitability_index=result_response.suitability_index,
        label=result_response.label,
        explanation=CropExplanationResponse(
            headline=explanation.headline,
            summary=explanation.summary,
            strengths=[
                CropExplanationFactorResponse(factor=s.factor, message=s.message)
                for s in explanation.strengths
            ],
            concerns=[
                CropExplanationFactorResponse(factor=c.factor, message=c.message)
                for c in explanation.concerns
            ],
            action=explanation.action,
            data_note=explanation.data_note,
            source=explanation.source,
            cached=explanation.cached,
        ),
    )

