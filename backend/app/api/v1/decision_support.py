"""Seasonal planning, climate-risk, and scenario endpoints."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from geoalchemy2.shape import to_shape
from pydantic import Field

from app.api.dependencies import get_farm_service
from app.api.schemas import BaseSchema, ErrorResponse
from app.services.decision_support import build_decision_support
from app.services.farm import FarmService


router = APIRouter(prefix="/farms", tags=["Decision support"])


class DecisionScenario(BaseSchema):
    selected_month: int = Field(default=1, ge=1, le=12)
    rainfall_change_pct: float = Field(default=0, ge=-50, le=50)
    temperature_change_c: float = Field(default=0, ge=-5, le=5)


@router.post(
    "/{farm_id}/decision-support",
    response_model=dict[str, Any],
    responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    summary="Calculate a seasonal plan, risk screening, and climate scenario",
)
async def calculate_decision_support(
    farm_id: UUID,
    scenario: DecisionScenario,
    farm_service: FarmService = Depends(get_farm_service),
) -> dict[str, Any]:
    farm = await farm_service.get_farm(farm_id)
    centroid = to_shape(farm.current_geometry.centroid)
    return build_decision_support(
        farm_id=str(farm.id),
        farm_name=farm.name,
        latitude=centroid.y,
        longitude=centroid.x,
        selected_month=scenario.selected_month,
        rainfall_change_pct=scenario.rainfall_change_pct,
        temperature_change_c=scenario.temperature_change_c,
    )
