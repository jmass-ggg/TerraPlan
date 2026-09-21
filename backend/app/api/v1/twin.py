"""
Twin API routes: analysis job triggering and snapshot retrieval.

POST  /api/v1/farms/{farm_id}/analysis-jobs  → 202 + Location
GET   /api/v1/farms/{farm_id}/digital-twin   → snapshot | pending | unavailable
GET   /api/v1/jobs/{job_id}                  → job progress

All routes go through `resolve_principal`; ownership-scoped 404 on wrong owner.

Requirements: 10.1, 10.2, 10.3, 1.5
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    get_app_settings,
    get_current_principal,
    get_request_session,
)
from app.api.schemas import ErrorResponse, ReadBaseSchema, persisted_datetime_to_utc
from app.core.config import Settings
from app.core.exceptions import NotFoundError
from app.core.security import Principal
from app.models.farm import Farm
from app.models.snapshot import AnalysisJob, AnalysisSnapshot, JobStatus
from app.services.snapshot_service import enqueue_analysis_job

router = APIRouter(tags=["Digital Twin"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class JobProgressResponse(ReadBaseSchema):
    """Per-job progress with stage-level details.

    Requirements: 1.5, 10.2
    """

    job_id: uuid.UUID = Field(description="Analysis job UUID")
    farm_id: uuid.UUID = Field(description="Farm UUID this job belongs to")
    geometry_revision: int = Field(description="Farm geometry revision at enqueue time")
    status: str = Field(description="Overall job status: queued | running | completed | failed")
    stages: dict[str, Any] = Field(description="Per-stage progress detail")
    error_message: str | None = Field(default=None, description="Error detail when status=failed")
    snapshot_id: uuid.UUID | None = Field(default=None, description="Snapshot UUID on completion")
    created_at: datetime = Field(description="Job creation time (UTC)")


class FarmSnapshotResponse(ReadBaseSchema):
    """Full environmental snapshot with provenance.

    Requirements: 7.1, 10.1, 10.4
    """

    model_config = {"protected_namespaces": ()}

    status: str = Field(description="ready | pending | unavailable")
    snapshot_id: uuid.UUID | None = Field(default=None)
    farm_id: uuid.UUID | None = Field(default=None)
    geometry_revision: int | None = Field(default=None)
    valid_time: datetime | None = Field(default=None, description="Snapshot valid time (UTC)")
    data_mode: str | None = Field(default=None)
    evidence_statuses: dict[str, str] | None = Field(default=None)
    model_version: str | None = Field(default=None)
    # Environmental payload sections
    weather: dict | None = Field(default=None)
    climate_baseline: dict | None = Field(default=None)
    satellite: dict | None = Field(default=None)
    soil: dict | None = Field(default=None)
    terrain: dict | None = Field(default=None)
    conduit: dict | None = Field(default=None)
    # Pending-state fields
    job_id: uuid.UUID | None = Field(default=None)
    job_status: str | None = Field(default=None)
    job_stages: dict[str, Any] | None = Field(default=None)


# ---------------------------------------------------------------------------
# Ownership helper
# ---------------------------------------------------------------------------


async def _require_owned_farm(
    farm_id: uuid.UUID,
    owner_id: uuid.UUID,
    session: AsyncSession,
) -> Farm:
    """Return the farm only if it is owned by `owner_id`.

    Raises NotFoundError (404) for both non-existent farms and farms owned
    by other users — indistinguishable, as required by 10.3.
    """
    result = await session.execute(
        select(Farm).where(Farm.id == farm_id, Farm.user_id == owner_id)
    )
    farm = result.scalar_one_or_none()
    if farm is None:
        raise NotFoundError("Farm not found")
    return farm


# ---------------------------------------------------------------------------
# POST /api/v1/farms/{farm_id}/analysis-jobs
# ---------------------------------------------------------------------------


@router.post(
    "/farms/{farm_id}/analysis-jobs",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        404: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    summary="Trigger an analysis job",
    description=(
        "Enqueue a new analysis job for the farm's current geometry revision. "
        "Returns 202 Accepted with a Location header pointing to the job progress endpoint. "
        "Requirements: 1.1, 10.2"
    ),
)
async def trigger_analysis_job(
    farm_id: uuid.UUID,
    response: Response,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_request_session),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    farm = await _require_owned_farm(farm_id, principal.user_id, session)

    job = await enqueue_analysis_job(
        farm_id=farm.id,
        geometry_revision=farm.current_geometry_revision,
        session=session,
        settings=settings,
    )
    await session.commit()

    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    return {"job_id": str(job.id), "status": job.status}


# ---------------------------------------------------------------------------
# GET /api/v1/farms/{farm_id}/digital-twin
# ---------------------------------------------------------------------------


@router.get(
    "/farms/{farm_id}/digital-twin",
    response_model=FarmSnapshotResponse,
    responses={404: {"model": ErrorResponse}},
    summary="Get the farm digital twin",
    description=(
        "Returns the latest completed snapshot for the current geometry revision, "
        "a 'pending' response if a job is running, or 'unavailable' if no job "
        "has ever been enqueued. Requirements: 7.4, 10.1, 10.3"
    ),
)
async def get_farm_digital_twin(
    farm_id: uuid.UUID,
    snapshot_id: uuid.UUID | None = Query(
        default=None,
        description="Request a historical snapshot by ID (Requirements: 7.5)",
    ),
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_request_session),
) -> FarmSnapshotResponse:
    farm = await _require_owned_farm(farm_id, principal.user_id, session)

    # If a specific snapshot_id was requested, return that snapshot directly
    if snapshot_id is not None:
        snap_result = await session.execute(
            select(AnalysisSnapshot).where(
                AnalysisSnapshot.id == snapshot_id,
                AnalysisSnapshot.farm_id == farm.id,
            )
        )
        snap = snap_result.scalar_one_or_none()
        if snap is None:
            raise NotFoundError("Snapshot not found")
        return _snapshot_to_response(snap)

    # Look for latest completed snapshot for the current geometry revision
    snap_result = await session.execute(
        select(AnalysisSnapshot)
        .where(
            AnalysisSnapshot.farm_id == farm.id,
            AnalysisSnapshot.geometry_revision == farm.current_geometry_revision,
        )
        .order_by(desc(AnalysisSnapshot.created_at))
        .limit(1)
    )
    snapshot = snap_result.scalar_one_or_none()

    if snapshot is not None:
        return _snapshot_to_response(snapshot)

    # No snapshot — check for a pending/running job
    job_result = await session.execute(
        select(AnalysisJob)
        .where(
            AnalysisJob.farm_id == farm.id,
            AnalysisJob.geometry_revision == farm.current_geometry_revision,
            AnalysisJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
        )
        .order_by(desc(AnalysisJob.created_at))
        .limit(1)
    )
    job = job_result.scalar_one_or_none()

    if job is not None:
        return FarmSnapshotResponse(
            status="pending",
            job_id=job.id,
            job_status=job.status,
            job_stages=job.stages,
        )

    return FarmSnapshotResponse(status="unavailable")


# ---------------------------------------------------------------------------
# GET /api/v1/jobs/{job_id}
# ---------------------------------------------------------------------------


@router.get(
    "/jobs/{job_id}",
    response_model=JobProgressResponse,
    responses={404: {"model": ErrorResponse}},
    summary="Get analysis job progress",
    description=(
        "Returns stage names and statuses for a job. "
        "Returns 404 for jobs owned by other users. Requirements: 1.5, 10.2"
    ),
)
async def get_job_progress(
    job_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_request_session),
) -> JobProgressResponse:
    # Load job with farm ownership check
    job_result = await session.execute(
        select(AnalysisJob)
        .join(Farm, Farm.id == AnalysisJob.farm_id)
        .where(
            AnalysisJob.id == job_id,
            Farm.user_id == principal.user_id,
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise NotFoundError("Job not found")

    return JobProgressResponse(
        job_id=job.id,
        farm_id=job.farm_id,
        geometry_revision=job.geometry_revision,
        status=job.status,
        stages=job.stages,
        error_message=job.error_message,
        snapshot_id=job.snapshot_id,
        created_at=persisted_datetime_to_utc(job.created_at),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _snapshot_to_response(snap: AnalysisSnapshot) -> FarmSnapshotResponse:
    return FarmSnapshotResponse(
        status="ready",
        snapshot_id=snap.id,
        farm_id=snap.farm_id,
        geometry_revision=snap.geometry_revision,
        valid_time=persisted_datetime_to_utc(snap.valid_time_utc),
        data_mode=snap.data_mode,
        evidence_statuses=snap.evidence_statuses,
        model_version=snap.model_version,
        weather=snap.weather,
        climate_baseline=snap.climate_baseline,
        satellite=snap.satellite,
        soil=snap.soil,
        terrain=snap.terrain,
        conduit=snap.conduit,
    )
