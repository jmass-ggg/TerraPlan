"""
Annual Planner API routes.

GET  /api/v1/farms/{farm_id}/crop-plan?year=YYYY
POST /api/v1/farms/{farm_id}/crop-plan/entries
PATCH /api/v1/farms/{farm_id}/crop-plan/entries/{entry_id}
DELETE /api/v1/farms/{farm_id}/crop-plan/entries/{entry_id}
POST /api/v1/farms/{farm_id}/crop-plan/proposals/{proposal_id}/accept
GET  /api/v1/farms/{farm_id}/crop-plan/export?year=YYYY

All routes use get_current_principal; 404 is returned for wrong owners.

Requirements: 2.1, 2.4, 2.5, 2.6, 4.3, 5.6
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_principal, get_request_session
from app.api.schemas import ErrorResponse, persisted_datetime_to_utc
from app.api.v1.planner_schemas import (
    AnnualPlanResponse,
    ChangeProposalResponse,
    ExportEntryRow,
    PlanEntryCreate,
    PlanEntryResponse,
    PlanEntryUpdate,
    PlanExportResponse,
    MonthRecommendationResponse,
    PerennialOpportunityResponse,
    TimelineItemResponse,
)
from app.core.security import Principal
from app.models.plan import ChangeProposal, PlanEntry
from app.services import planner_service
from app.services.planner_service import (
    PlanEntryCreate as ServiceEntryCreate,
    PlanEntryUpdate as ServiceEntryUpdate,
)

router = APIRouter(tags=["Annual Planner"])


# ---------------------------------------------------------------------------
# Domain → API response converters
# ---------------------------------------------------------------------------


def _entry_to_response(entry: PlanEntry) -> PlanEntryResponse:
    return PlanEntryResponse(
        revision=getattr(entry, "revision", 1),
        field_name=getattr(entry, "field_name", None),
        id=entry.id,
        farm_id=entry.farm_id,
        crop_name=entry.crop_name,
        planting_date=entry.planting_date,
        harvest_date=entry.harvest_date,
        cultivation_mode=entry.cultivation_mode,
        irrigation_mm=entry.irrigation_mm,
        area_ha=entry.area_ha,
        snapshot_id=entry.snapshot_id,
        data_mode=entry.data_mode,
        suitability_index=entry.suitability_index,
        engine_version=entry.engine_version,
        created_at=persisted_datetime_to_utc(entry.created_at),
        updated_at=persisted_datetime_to_utc(entry.updated_at),
    )


def _proposal_to_response(proposal: ChangeProposal) -> ChangeProposalResponse:
    return ChangeProposalResponse(
        id=proposal.id,
        farm_id=proposal.farm_id,
        entry_id=proposal.entry_id,
        old_suitability_index=proposal.old_suitability_index,
        new_suitability_index=proposal.new_suitability_index,
        changed_inputs=proposal.changed_inputs,
        new_snapshot_id=proposal.new_snapshot_id,
        issue_date=persisted_datetime_to_utc(proposal.issue_date),
        status=proposal.status.value if hasattr(proposal.status, "value") else proposal.status,
        created_at=persisted_datetime_to_utc(proposal.created_at),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/farms/{farm_id}/crop-plan",
    response_model=AnnualPlanResponse,
    status_code=status.HTTP_200_OK,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse, "description": "Farm not found or not owned by user"},
    },
    summary="Get annual crop plan",
    description=(
        "Returns the 12-month recommendation grid, all saved Plan_Entries for the "
        "requested year, and any pending Change_Proposals. "
        "Requirements: 1.1, 1.2, 1.3, 1.4, 2.4"
    ),
)
async def get_annual_plan(
    farm_id: UUID,
    year: int = Query(description="Calendar year", ge=1900, le=2100),
    rainfall_change_pct: float = Query(default=0, ge=-50, le=50),
    temperature_change_c: float = Query(default=0, ge=-5, le=5),
    irrigation_mm: float | None = Query(default=None, ge=0),
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_request_session),
) -> AnnualPlanResponse:
    result = await planner_service.get_annual_plan(
        session=session,
        principal=principal,
        farm_id=farm_id,
        year=year,
        rainfall_change_pct=rainfall_change_pct,
        temperature_change_c=temperature_change_c,
        irrigation_mm=irrigation_mm,
    )

    months = [
        MonthRecommendationResponse(
            month=m.month,
            month_name=m.month_name,
            data_mode=m.data_mode,
            snapshot_id=m.snapshot_id,
            recommendations=m.recommendations,
        )
        for m in result.months
    ]

    return AnnualPlanResponse(
        farm_id=result.farm_id,
        year=result.year,
        months=months,
        timeline=[TimelineItemResponse(**item.__dict__) for item in result.timeline],
        perennial_opportunities=[
            PerennialOpportunityResponse(**item.__dict__)
            for item in result.perennial_opportunities
        ],
        entries=[_entry_to_response(e) for e in result.entries],
        proposals=[_proposal_to_response(p) for p in result.proposals],
        solver_status=result.solver_status,
        fallback_used=result.fallback_used,
        explanation=result.explanation,
    )


@router.post(
    "/farms/{farm_id}/crop-plan/entries",
    response_model=PlanEntryResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse, "description": "Farm not found or not owned by user"},
        422: {"model": ErrorResponse, "description": "Validation error or overlap conflict"},
    },
    summary="Add a plan entry",
    description=(
        "Creates a new Plan_Entry. Derives harvest_date from planting_date + "
        "crop.duration_months. Returns 422 when the entry overlaps an existing one. "
        "Requirements: 2.1, 2.2, 2.3, 3.1"
    ),
)
async def create_plan_entry(
    farm_id: UUID,
    data: PlanEntryCreate,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_request_session),
) -> PlanEntryResponse:
    entry = await planner_service.create_entry(
        session=session,
        principal=principal,
        farm_id=farm_id,
        data=ServiceEntryCreate(
            crop_name=data.crop_name,
            planting_date=data.planting_date,
            cultivation_mode=data.cultivation_mode,
            irrigation_mm=data.irrigation_mm,
            area_ha=data.area_ha,
            field_name=data.field_name,
        ),
    )
    return _entry_to_response(entry)


@router.patch(
    "/farms/{farm_id}/crop-plan/entries/{entry_id}",
    response_model=PlanEntryResponse,
    status_code=status.HTTP_200_OK,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse, "description": "Farm or entry not found / wrong owner"},
        422: {"model": ErrorResponse, "description": "Validation error or overlap conflict"},
    },
    summary="Update a plan entry",
    description=(
        "Updates an existing Plan_Entry. Re-derives harvest_date when planting_date "
        "changes and re-validates overlaps. "
        "Requirements: 2.5, 3.3"
    ),
)
async def update_plan_entry(
    farm_id: UUID,
    entry_id: UUID,
    data: PlanEntryUpdate,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_request_session),
) -> PlanEntryResponse:
    entry = await planner_service.update_entry(
        session=session,
        principal=principal,
        farm_id=farm_id,
        entry_id=entry_id,
        data=ServiceEntryUpdate(
            expected_revision=data.expected_revision,
            planting_date=data.planting_date,
            cultivation_mode=data.cultivation_mode,
            irrigation_mm=data.irrigation_mm,
            area_ha=data.area_ha,
            field_name=data.field_name,
        ),
    )
    return _entry_to_response(entry)


@router.delete(
    "/farms/{farm_id}/crop-plan/entries/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse, "description": "Farm or entry not found / wrong owner"},
    },
    summary="Delete a plan entry",
    description="Removes a saved Plan_Entry. Requirements: 2.6",
)
async def delete_plan_entry(
    farm_id: UUID,
    entry_id: UUID,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_request_session),
) -> Response:
    await planner_service.delete_entry(
        session=session,
        principal=principal,
        farm_id=farm_id,
        entry_id=entry_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/farms/{farm_id}/crop-plan/proposals/{proposal_id}/accept",
    response_model=PlanEntryResponse,
    status_code=status.HTTP_200_OK,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse, "description": "Farm/proposal not found or wrong owner"},
    },
    summary="Accept a change proposal",
    description=(
        "Applies a pending Change_Proposal to its linked Plan_Entry and marks the "
        "proposal as accepted. Requirements: 4.3"
    ),
)
async def accept_change_proposal(
    farm_id: UUID,
    proposal_id: UUID,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_request_session),
) -> PlanEntryResponse:
    entry = await planner_service.accept_proposal(
        session=session,
        principal=principal,
        farm_id=farm_id,
        proposal_id=proposal_id,
    )
    return _entry_to_response(entry)


@router.post(
    "/farms/{farm_id}/crop-plan/proposals/{proposal_id}/dismiss",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse, "description": "Farm/proposal not found or wrong owner"},
    },
    summary="Dismiss a change proposal",
    description=(
        "Marks a pending Change_Proposal as dismissed, leaving the linked "
        "Plan_Entry unchanged. Requirements: 4.4"
    ),
)
async def dismiss_change_proposal(
    farm_id: UUID,
    proposal_id: UUID,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_request_session),
) -> Response:
    await planner_service.dismiss_proposal(
        session=session,
        principal=principal,
        farm_id=farm_id,
        proposal_id=proposal_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/farms/{farm_id}/crop-plan/export",
    response_model=PlanExportResponse,
    status_code=status.HTTP_200_OK,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse, "description": "Farm not found or not owned by user"},
    },
    summary="Export annual plan",
    description=(
        "Returns farm name, year, data_mode, all saved entries, and generation "
        "timestamp for external export. Requirements: 5.6"
    ),
)
async def export_annual_plan(
    farm_id: UUID,
    year: int = Query(description="Calendar year", ge=1900, le=2100),
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_request_session),
) -> PlanExportResponse:
    result = await planner_service.get_annual_plan(
        session=session,
        principal=principal,
        farm_id=farm_id,
        year=year,
    )

    # Determine dominant data_mode from entries (fall back to first month's mode)
    if result.entries:
        data_mode = result.entries[0].data_mode
    elif result.months:
        data_mode = result.months[0].data_mode
    else:
        data_mode = "demonstration"

    # Re-load farm name (already ownership-checked by get_annual_plan)
    from sqlalchemy import select
    from app.models.farm import Farm

    farm_result = await session.execute(
        select(Farm).where(Farm.id == farm_id)
    )
    farm = farm_result.scalar_one()

    rows = [
        ExportEntryRow(
            crop_name=e.crop_name,
            planting_date=str(e.planting_date),
            harvest_date=str(e.harvest_date),
            cultivation_mode=e.cultivation_mode,
            irrigation_mm=e.irrigation_mm,
            area_ha=e.area_ha,
            suitability_index=e.suitability_index,
            data_mode=e.data_mode,
        )
        for e in result.entries
    ]

    return PlanExportResponse(
        farm_id=str(farm_id),
        farm_name=farm.name,
        year=year,
        data_mode=data_mode,
        entries=rows,
        created_at=datetime.now(timezone.utc),
    )
