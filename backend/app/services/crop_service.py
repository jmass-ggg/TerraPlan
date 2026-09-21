"""
Crop simulation service.

Orchestrates farm ownership checks, snapshot loading, context building,
and crop engine calls to produce a SimulationResponse or CropRankingResponse.

Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 4.1, 4.2
"""

from __future__ import annotations

import logging
from datetime import date
from uuid import UUID

from geoalchemy2.shape import to_shape
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.crop_schemas import (
    ComponentScoresResponse,
    CropRankingResponse,
    SimulationResponse,
    SimulationResultResponse,
)
from app.core.exceptions import FarmValidationError, NotFoundError
from app.core.security import Principal
from app.domain.crop_engine import ENGINE_VERSION, SimulationResult, rank_all, score
from app.domain.crop_register import CROP_REGISTER
from app.domain.snapshot_context import context_from_demonstration, context_from_snapshot, with_irrigation
from app.core.config import Settings
from app.models.farm import Farm
from app.models.snapshot import AnalysisSnapshot
from app.repositories.farm import FarmRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _result_to_response(result: SimulationResult) -> SimulationResultResponse:
    """Convert a domain SimulationResult to its API response schema."""
    return SimulationResultResponse(
        crop_name=result.crop_name,
        suitability_index=result.suitability_index,
        label=result.label,
        components=ComponentScoresResponse(
            temperature=int(round(result.components.temperature)) if result.components.temperature is not None else None,
            water=int(round(result.components.water)) if result.components.water is not None else None,
            soil=int(round(result.components.soil)) if result.components.soil is not None else None,
            heat_safety=int(round(result.components.heat_safety)) if result.components.heat_safety is not None else None,
            drought_flood_safety=int(round(result.components.drought_flood_safety)) if result.components.drought_flood_safety is not None else None,
            environmental_condition=int(round(result.components.environmental_condition)) if result.components.environmental_condition is not None else None,
        ),
        limiting_factor=result.limiting_factor,
        reason=result.reason,
        hard_exclusion=result.hard_exclusion,
        hard_exclusion_reason=result.hard_exclusion_reason,
        engine_version=result.engine_version,
        snapshot_id=result.snapshot_id,
        data_mode=result.data_mode,
        input_completeness=result.input_completeness,
    )


async def _load_latest_snapshot(
    session: AsyncSession,
    farm_id: UUID,
    geometry_revision: int,
) -> AnalysisSnapshot | None:
    """Return the latest completed snapshot for the current geometry revision, or None."""
    result = await session.execute(
        select(AnalysisSnapshot)
        .where(
            AnalysisSnapshot.farm_id == farm_id,
            AnalysisSnapshot.geometry_revision == geometry_revision,
        )
        .order_by(desc(AnalysisSnapshot.created_at))
        .limit(1)
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def simulate(
    session: AsyncSession,
    principal: Principal,
    farm_id: UUID,
    crop_name: str | None,
    planting_date: date,
    cultivation_mode: str,
    irrigation_mm: float | None,
) -> SimulationResponse | CropRankingResponse:
    """
    Simulate crop suitability for *farm_id* under the given planting parameters.

    Steps:
    1. Fetch the farm via FarmRepository (ownership check).
    2. Attempt to load the latest AnalysisSnapshot for the current geometry revision.
    3. Build SnapshotContext from snapshot or demonstration fallback.
    4. Score one crop (returns SimulationResponse + 3 alternatives) or rank all
       crops (returns CropRankingResponse) depending on whether crop_name was supplied.

    Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 4.1, 4.2
    """
    # 1. Ownership-scoped farm fetch (raises NotFoundError → 404 for wrong owner)
    repo = FarmRepository(session, principal.user_id)
    farm: Farm = await repo.get_by_id(farm_id)

    centroid = to_shape(farm.current_geometry.centroid)

    # 2. Attempt to load latest snapshot
    snapshot = await _load_latest_snapshot(
        session, farm.id, farm.current_geometry_revision
    )

    if snapshot is None and Settings().data_mode.value != "demonstration":
        raise FarmValidationError(field="snapshot", detail_code="INSUFFICIENT_EVIDENCE",
                                  message="Run farm analysis before requesting crop recommendations.")
    results = []
    for crop in CROP_REGISTER:
        context = (context_from_snapshot(snapshot, planting_date.month, crop.duration_months, planting_date)
                   if snapshot is not None else context_from_demonstration(centroid.y, centroid.x, planting_date.month, crop.duration_months))
        context = with_irrigation(context, cultivation_mode, irrigation_mm, crop.duration_months)
        results.append(score(crop, context))
    results.sort(key=lambda r: (-(r.suitability_index if r.suitability_index is not None else -1), r.crop_name))
    metadata = dict(farm_id=str(farm.id), engine_version=ENGINE_VERSION,
                    snapshot_id=context.snapshot_id, data_mode=context.data_mode)
    if crop_name is None:
        return CropRankingResponse(**metadata, ranked=[_result_to_response(r) for r in results])
    selected = next((r for r in results if r.crop_name.lower() == crop_name.lower()), None)
    if selected is None:
        raise FarmValidationError(field="body.crop_name", detail_code="INVALID_VALUE", message=f"Unknown crop: {crop_name}")
    return SimulationResponse(**metadata, selected=_result_to_response(selected),
                              alternatives=[_result_to_response(r) for r in results if r is not selected][:3])
