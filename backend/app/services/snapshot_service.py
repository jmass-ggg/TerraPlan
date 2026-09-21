"""
Snapshot service — enqueue analysis jobs and run them to produce
immutable Farm_Snapshot records.

Public API:
  enqueue_analysis_job(farm_id, geometry_revision, session)
      → Creates AnalysisJob in the DB and publishes to Redis.
        Returns the AnalysisJob with id and status="queued".

  run_analysis_job(job_id, settings, session_factory)
      → Worker entry point. Marks job running, runs all adapters
        concurrently, persists AnalysisSnapshot, marks job completed.
        On unrecoverable error: marks job failed with error_message.

Requirements: 1.1, 1.2, 1.3, 7.1, 7.2, 7.6
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from geoalchemy2.shape import to_shape
from shapely.geometry import Point, mapping
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.data.providers import climate, satellite, soil, terrain, weather
from app.data.providers import conduit_eligibility
from app.data.providers.base import (
    EVIDENCE_ACCEPTED,
    EVIDENCE_ERROR,
    EVIDENCE_UNAVAILABLE,
    ProviderResult,
)
from app.models.farm import Farm, FarmGeometryRevision
from app.models.snapshot import AnalysisJob, AnalysisSnapshot, JobStatus

logger = logging.getLogger(__name__)

# Redis queue key used by the worker process
REDIS_JOB_QUEUE_KEY = "farmtwin:jobs"

# Model version string for snapshot records
MODEL_VERSION = "farmtwin-twin-v1"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _stage_entry(status: str = "queued") -> dict:
    return {
        "status": status,
        "started_at": None,
        "completed_at": None,
    }


def _stage_started(now: str) -> dict:
    return {"status": "running", "started_at": now, "completed_at": None}


def _stage_done(started_at: str, now: str) -> dict:
    return {"status": "completed", "started_at": started_at, "completed_at": now}


def _stage_failed(started_at: str | None, now: str, error: str) -> dict:
    return {
        "status": "failed",
        "started_at": started_at,
        "completed_at": now,
        "error": error,
    }


async def _publish_to_redis(job_id: uuid.UUID, settings: Settings) -> None:
    """Publish a job ID to the Redis queue.

    Failure to publish is non-fatal: the farm is already saved.
    The caller logs a warning and proceeds without raising.

    Requirements: 1.1, 1.3
    """
    try:
        import redis.asyncio as aioredis  # type: ignore[import-untyped]

        redis_url = settings.redis_url
        async with aioredis.from_url(redis_url) as client:
            payload = json.dumps({"job_id": str(job_id)})
            await client.rpush(REDIS_JOB_QUEUE_KEY, payload)
            logger.info("Enqueued analysis job %s on Redis.", job_id)
    except Exception as exc:
        logger.warning(
            "Failed to publish analysis job %s to Redis: %s. "
            "Farm is saved; job remains in DB with status='queued'.",
            job_id,
            exc,
        )


# ---------------------------------------------------------------------------
# Enqueue
# ---------------------------------------------------------------------------

async def enqueue_analysis_job(
    farm_id: uuid.UUID,
    geometry_revision: int,
    session: AsyncSession,
    settings: Settings | None = None,
) -> AnalysisJob:
    """Create an AnalysisJob and publish it to the Redis queue.

    This function should be called from FarmService after a successful
    farm create or geometry update (within the same or a subsequent
    transaction, depending on the caller's isolation requirements).

    The job is persisted to the DB first; Redis publishing is best-effort.
    If Redis is unavailable the farm operation is not rolled back — the job
    record in the DB allows a worker to pick it up after a restart.

    Args:
        farm_id: UUID of the farm the job belongs to.
        geometry_revision: The farm geometry revision at enqueue time.
        session: Active async SQLAlchemy session. The caller must commit.
        settings: Application settings (for Redis URL). May be None in
            contexts where Redis publishing is handled externally.

    Returns:
        The newly created AnalysisJob (status=queued).

    Requirements: 1.1, 1.2, 1.3
    """
    # Serialize enqueue requests for a farm; coalesce duplicate active requests.
    await session.execute(select(Farm.id).where(Farm.id == farm_id).with_for_update())
    active = await session.execute(select(AnalysisJob).where(
        AnalysisJob.farm_id == farm_id, AnalysisJob.geometry_revision == geometry_revision,
        AnalysisJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
    ).order_by(AnalysisJob.created_at.desc()).limit(1))
    existing = active.scalar_one_or_none()
    if existing is not None:
        return existing
    job = AnalysisJob(
        id=uuid.uuid4(),
        farm_id=farm_id,
        geometry_revision=geometry_revision,
        status=JobStatus.QUEUED,
        stages={
            "weather": _stage_entry(),
            "climate": _stage_entry(),
            "satellite": _stage_entry(),
            "soil": _stage_entry(),
            "terrain": _stage_entry(),
            "conduit": _stage_entry(),
        },
        error_message=None,
        snapshot_id=None,
    )
    session.add(job)
    await session.flush()

    # The committed database row is the durable queue. No publish-before-commit race.

    return job


# ---------------------------------------------------------------------------
# Run (worker entry point)
# ---------------------------------------------------------------------------

async def run_analysis_job(
    job_id: uuid.UUID,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> AnalysisSnapshot | None:
    """Worker entry point: run all adapters and persist an AnalysisSnapshot.

    Steps:
    1. Load job and farm geometry.
    2. Mark job running.
    3. Run all provider adapters concurrently.
    4. Assess Conduit eligibility.
    5. Persist AnalysisSnapshot.
    6. Mark job completed with snapshot_id.

    On unrecoverable error: mark job failed, persist partial evidence_statuses.

    Returns the persisted AnalysisSnapshot on success, None on failure.

    Requirements: 1.1, 1.2, 1.3, 7.1, 7.2, 7.6
    """
    async with session_factory() as session:
        async with session.begin():
            result = await session.execute(
                select(AnalysisJob).where(AnalysisJob.id == job_id).with_for_update()
            )
            job: AnalysisJob | None = result.scalar_one_or_none()
            if job is None:
                logger.error("run_analysis_job: job %s not found.", job_id)
                return None

            now_utc = datetime.now(timezone.utc)
            if job.status == JobStatus.COMPLETED:
                return await session.get(AnalysisSnapshot, job.snapshot_id)
            if job.attempts >= 3 or (job.lease_until and job.lease_until > now_utc):
                return None
            job.attempts += 1
            attempt = job.attempts
            job.lease_until = now_utc + timedelta(minutes=10)
            job.error_message = None
            # Load farm geometry revision
            rev_result = await session.execute(
                select(FarmGeometryRevision).where(
                    FarmGeometryRevision.farm_id == job.farm_id,
                    FarmGeometryRevision.revision == job.geometry_revision,
                )
            )
            revision: FarmGeometryRevision | None = rev_result.scalar_one_or_none()
            if revision is None:
                msg = (
                    f"Geometry revision {job.geometry_revision} not found "
                    f"for farm {job.farm_id}."
                )
                logger.error(msg)
                job.status = JobStatus.FAILED
                job.error_message = msg
                await session.flush()
                return None

            # Mark job running
            now = _iso_now()
            job.status = JobStatus.RUNNING
            job.stages = {stage: _stage_entry() for stage in job.stages}
            await session.flush()

            # Extract centroid and polygon from geometry revision
            centroid_shape = to_shape(revision.centroid)
            farm_centroid = Point(centroid_shape.x, centroid_shape.y)

            polygon_shape = to_shape(revision.geometry)
            farm_area_ha: float = revision.hectares

    # ------------------------------------------------------------------
    # Run provider adapters concurrently (outside the mark-running txn)
    # ------------------------------------------------------------------
    job_start = _iso_now()

    logger.info("Starting analysis job %s (farm=%s, rev=%s).", job_id, job.farm_id, job.geometry_revision)

    # Each provider call is wrapped to capture exceptions as ProviderResult.error
    async def update_stage(name: str, entry: dict):
        async with session_factory() as stage_session:
            async with stage_session.begin():
                row = (await stage_session.execute(select(AnalysisJob).where(AnalysisJob.id == job_id).with_for_update())).scalar_one()
                if row.attempts == attempt:
                    row.stages = {**row.stages, name: entry}

    async def _safe_fetch(name: str, coro) -> tuple[str, ProviderResult]:
        started = _iso_now()
        await update_stage(name, _stage_started(started))
        try:
            async with asyncio.timeout(300):
                result = await coro
            now = _iso_now()
            if result.evidence_status == EVIDENCE_ACCEPTED:
                stage_entry = _stage_done(started, now)
            else:
                # unavailable / ineligible are valid terminal states — not an error
                error_detail = result.error_message or result.evidence_status
                stage_entry = _stage_failed(started, now, error_detail)
            await update_stage(name, stage_entry)
            return name, result
        except Exception as exc:
            msg = f"{name} adapter raised unexpectedly: {exc}"
            logger.exception(msg)
            await update_stage(name, _stage_failed(started, _iso_now(), msg))
            return name, ProviderResult.error(msg)

    data_mode = settings.data_mode.value

    # Build the gather tasks (terrain first so conduit can use it)
    tasks = [
        _safe_fetch(
            "weather",
            weather.fetch(
                centroid_lat=farm_centroid.y,
                centroid_lon=farm_centroid.x,
                data_mode=data_mode,
            ),
        ),
        _safe_fetch(
            "climate",
            climate.fetch(
                centroid_lat=farm_centroid.y,
                centroid_lon=farm_centroid.x,
                data_mode=data_mode,
            ),
        ),
        _safe_fetch(
            "satellite",
            satellite.fetch(
                farm_polygon=polygon_shape,
                data_mode=data_mode,
                cdse_username=settings.cdse_username,
                cdse_password=settings.cdse_password.get_secret_value(),
            ),
        ),
        _safe_fetch(
            "soil",
            soil.fetch(
                centroid_lat=farm_centroid.y,
                centroid_lon=farm_centroid.x,
                farm_area_ha=farm_area_ha,
                data_mode=data_mode,
            ),
        ),
        _safe_fetch(
            "terrain",
            terrain.fetch(
                farm_polygon=polygon_shape,
                data_mode=data_mode,
            ),
        ),
    ]

    if data_mode == "live":
        raw_results = await asyncio.gather(*tasks, return_exceptions=False)
    else:
        # Non-live mode: cancel the provider coroutines cleanly and return
        # unavailable results for all stages without making any external calls.
        for task_coro in tasks:
            task_coro.close()
        raw_results = [
            (name, ProviderResult(
                payload=None,
                evidence_status=EVIDENCE_UNAVAILABLE,
                error_message="Live acquisition is disabled in this data mode.",
            ))
            for name in ("weather", "climate", "satellite", "soil", "terrain")
        ]


    adapter_results: dict[str, ProviderResult] = dict(raw_results)

    # Conduit eligibility needs the terrain result, so run sequentially after
    terrain_result = adapter_results.get("terrain")
    terrain_payload = (
        terrain_result.payload if terrain_result is not None else None
    )

    async with session_factory() as conduit_session:
        _, conduit_result = await _safe_fetch(
            "conduit",
            conduit_eligibility.fetch(
                farm_centroid=farm_centroid,
                session=conduit_session,
                terrain_payload=terrain_payload,
                data_mode=data_mode,
            ),
        )
    adapter_results["conduit"] = conduit_result

    # ------------------------------------------------------------------
    # Build evidence_statuses and determine overall data_mode
    # ------------------------------------------------------------------
    evidence_statuses: dict[str, str] = {}
    for name, pr in adapter_results.items():
        evidence_statuses[name] = pr.evidence_status

    # Determine snapshot data_mode:
    # If any source returned "accepted" live data → "live"
    # If all accepted are historical_replay → "historical_replay"
    # Fallback to the settings data mode
    snapshot_data_mode = data_mode

    # ------------------------------------------------------------------
    # Persist snapshot and update job status
    # ------------------------------------------------------------------
    now_utc = datetime.now(tz=timezone.utc)

    try:
        async with session_factory() as persist_session:
            async with persist_session.begin():
                # Reload job for update
                job_result = await persist_session.execute(
                    select(AnalysisJob).where(AnalysisJob.id == job_id)
                )
                db_job: AnalysisJob = job_result.scalar_one()
                if db_job.status == JobStatus.COMPLETED or db_job.attempts != attempt:
                    return None

                # Build per-stage completion — use the final adapter_results
                # so the committed stage dict matches what actually happened.
                completion_now = _iso_now()
                final_stages: dict[str, dict] = {}
                for stage_name, pr in adapter_results.items():
                    if pr.evidence_status == EVIDENCE_ACCEPTED:
                        final_stages[stage_name] = _stage_done(job_start, completion_now)
                    else:
                        final_stages[stage_name] = _stage_failed(
                            job_start,
                            completion_now,
                            pr.error_message or pr.evidence_status,
                        )
                db_job.stages = final_stages

                snapshot = AnalysisSnapshot(
                    id=uuid.uuid4(),
                    farm_id=db_job.farm_id,
                    geometry_revision=db_job.geometry_revision,
                    job_id=db_job.id,
                    valid_time_utc=now_utc,
                    data_mode=snapshot_data_mode,
                    weather=adapter_results["weather"].payload or None,
                    climate_baseline=adapter_results["climate"].payload or None,
                    satellite=adapter_results["satellite"].payload or None,
                    soil=adapter_results["soil"].payload or None,
                    terrain=adapter_results["terrain"].payload or None,
                    conduit=adapter_results["conduit"].payload or None,
                    evidence_statuses=evidence_statuses,
                    model_version=MODEL_VERSION,
                )
                persist_session.add(snapshot)
                await persist_session.flush()

                try:
                    from app.services.planner_service import generate_proposals
                    await generate_proposals(persist_session, snapshot)
                except Exception as proposal_exc:
                    # Proposals are best-effort — a proposal generation failure
                    # must not prevent the snapshot from being saved.
                    logger.warning(
                        "generate_proposals failed for snapshot %s (non-fatal): %s",
                        snapshot.id,
                        proposal_exc,
                    )

                db_job.lease_until = None
                db_job.status = JobStatus.COMPLETED
                db_job.snapshot_id = snapshot.id
                await persist_session.flush()

                snapshot_id = snapshot.id

        logger.info(
            "Analysis job %s completed. Snapshot %s persisted.",
            job_id,
            snapshot_id,
        )
        return snapshot

    except Exception as exc:
        msg = f"Failed to persist snapshot for job {job_id}: {exc}"
        logger.exception(msg)

        # Mark job failed with partial evidence_statuses
        try:
            async with session_factory() as fail_session:
                async with fail_session.begin():
                    fail_result = await fail_session.execute(
                        select(AnalysisJob).where(AnalysisJob.id == job_id)
                    )
                    fail_job: AnalysisJob = fail_result.scalar_one()
                    if fail_job.attempts != attempt:
                        return None
                    fail_job.lease_until = datetime.now(timezone.utc) + timedelta(seconds=30)
                    fail_job.status = JobStatus.FAILED
                    fail_job.error_message = msg
                    # Persist partial evidence_statuses in stages field
                    fail_job.stages = {
                        name: {
                            "status": pr.evidence_status,
                            "error": pr.error_message,
                        }
                        for name, pr in adapter_results.items()
                    }
                    await fail_session.flush()
        except Exception as inner_exc:
            logger.error(
                "Also failed to mark job %s as failed: %s", job_id, inner_exc
            )

        return None
