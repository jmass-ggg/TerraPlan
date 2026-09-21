"""
Conduit Ingestion Service.

Orchestrates the full fixture ingestion pipeline:
  parse → normalise/QC → deduplicate → bulk insert → aggregate

Transaction ownership:
- Transaction 1 (observations): checksum check, IngestionRun creation,
  NormalizedObservation bulk insert, IngestionRun finalization.
- Transaction 2 (aggregates): HourlyAggregate and DailyAggregate recompute.

The service refuses to run when DATA_MODE is live (Requirement 12.3).

Requirements: 1.1–1.5, 10.1–10.5, 12.3
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import DataMode, Settings
from app.data.conduit.aggregator import (
    compute_daily_aggregates,
    compute_hourly_aggregates,
)
from app.data.conduit.deduplicator import deduplicate
from app.data.conduit.normalizer import NORMALIZER_VERSION, normalize_and_qc
from app.data.conduit.parser import (
    PARSER_VERSION,
    ConduitFormatError,
    parse_fixture,
)
from app.models.conduit import IngestionRun, IngestionStatus, Station

logger = logging.getLogger(__name__)

# Default station used for the historical fixture.
_FIXTURE_STATION_PROVIDER_ID = "conduit-main"
_FIXTURE_STATION_NAME = "Conduit Main Station"
_FIXTURE_SOURCE_ID = "fixture"


class IngestionConfigError(Exception):
    """Raised when the ingestion service cannot run due to configuration."""


class IngestionError(Exception):
    """Raised when an ingestion run fails unrecoverably."""


async def _get_or_create_station(session: AsyncSession, settings: Settings) -> Station:
    """Return the fixture station, creating it if it doesn't exist yet.

    Coordinates are taken from settings so they can be configured per-deployment
    without touching code. If the row already exists but lacks coordinates
    (created by an older version), update it in place.
    """
    result = await session.execute(
        select(Station).where(
            Station.provider_station_id == _FIXTURE_STATION_PROVIDER_ID
        )
    )
    station = result.scalar_one_or_none()

    lat = settings.conduit_station_latitude
    lon = settings.conduit_station_longitude
    elev = settings.conduit_station_elevation_m

    if station is not None:
        # Back-fill coordinates if they were missing (idempotent).
        if station.latitude is None or station.longitude is None:
            station.latitude = lat
            station.longitude = lon
            station.elevation_m = elev
            await session.flush()
        return station

    station = Station(
        provider_station_id=_FIXTURE_STATION_PROVIDER_ID,
        name=_FIXTURE_STATION_NAME,
        provider="conduit",
        latitude=lat,
        longitude=lon,
        elevation_m=elev,
    )
    session.add(station)
    await session.flush()  # assign UUID without committing
    return station


async def _find_existing_run(
    session: AsyncSession, checksum: str
) -> IngestionRun | None:
    """Check for a previously completed run with the same SHA-256 checksum.

    Requirement 10.5
    """
    result = await session.execute(
        select(IngestionRun).where(
            IngestionRun.sha256_checksum == checksum,
            IngestionRun.status == IngestionStatus.COMPLETED,
        )
    )
    return result.scalar_one_or_none()


def _compute_checksum(content: bytes) -> str:
    """Return the SHA-256 hex digest of *content*."""
    return hashlib.sha256(content).hexdigest()


async def ingest_fixture(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    fixture_path: Path,
) -> IngestionRun:
    """Run the full Conduit fixture ingestion pipeline.

    Parameters
    ----------
    session_factory:
        Factory for creating database sessions. The service opens its own
        sessions so the caller does not need to manage a session lifecycle.
    settings:
        Application settings. Must have ``data_mode != DataMode.LIVE``
        (Requirement 12.3).
    fixture_path:
        Path to the JSON fixture file on disk.

    Returns
    -------
    IngestionRun
        The completed (or duplicate_run) run record. All scalar attributes
        are materialized before the session closes.

    Raises
    ------
    IngestionConfigError
        When ``DATA_MODE`` is ``live`` or the fixture file is not found.
    IngestionError
        When an unrecoverable error occurs during the pipeline (database
        unavailable, format error, etc.).
    """
    # ------------------------------------------------------------------
    # Requirement 12.3 — refuse to run when DATA_MODE=live
    # ------------------------------------------------------------------
    if settings.data_mode == DataMode.LIVE:
        raise IngestionConfigError(
            "ingest_fixture refused: DATA_MODE is 'live'. "
            "Historical fixture ingestion is only permitted in "
            "'historical_replay' or 'demonstration' modes."
        )

    # ------------------------------------------------------------------
    # Read fixture file  (Requirement 10.1)
    # ------------------------------------------------------------------
    if not fixture_path.exists():
        raise IngestionConfigError(
            f"Fixture file not found: {fixture_path}"
        )

    raw_bytes = fixture_path.read_bytes()
    raw_json = raw_bytes.decode("utf-8")
    payload_size = len(raw_bytes)
    checksum = _compute_checksum(raw_bytes)

    logger.info(
        "ingest_fixture: fixture=%s size=%d checksum=%s",
        fixture_path,
        payload_size,
        checksum,
    )

    start_time = time.monotonic()

    # ------------------------------------------------------------------
    # Transaction 1: checksum check → insert observations
    # ------------------------------------------------------------------
    async with session_factory() as session:
        async with session.begin():
            # Requirement 10.5 — skip re-ingestion on matching checksum
            existing_run = await _find_existing_run(session, checksum)
            if existing_run is not None:
                logger.info(
                    "Duplicate run detected: checksum %s matches run %s. "
                    "Returning existing record.",
                    checksum,
                    existing_run.id,
                )
                # Materialize before session closes
                run_id = existing_run.id
                run_status = existing_run.status
                run_accepted = existing_run.accepted_count
                run_rejected = existing_run.rejected_count
                run_duplicate = existing_run.duplicate_count

            if existing_run is not None:
                # Build a lightweight stand-in so callers get a proper object
                # with all scalar fields populated.
                return_run = IngestionRun(
                    id=run_id,
                    source_id=_FIXTURE_SOURCE_ID,
                    retrieval_time=existing_run.retrieval_time,
                    payload_size_bytes=payload_size,
                    sha256_checksum=checksum,
                    parser_version=PARSER_VERSION,
                    normalizer_version=NORMALIZER_VERSION,
                    accepted_count=run_accepted,
                    rejected_count=run_rejected,
                    duplicate_count=run_duplicate,
                    status=IngestionStatus.DUPLICATE_RUN,
                )
                return return_run

            retrieval_time = datetime.now(timezone.utc)

            # ------------------------------------------------------------------
            # Parse  (Requirements 2.1–2.7, 3.1, 3.5)
            # ------------------------------------------------------------------
            try:
                parsed_observations, parse_report = parse_fixture(raw_json)
            except ConduitFormatError as exc:
                raise IngestionError(
                    f"Fixture format error: {exc}"
                ) from exc

            logger.info(
                "Parse complete: accepted=%d rejected=%d field_errors=%d",
                parse_report.accepted,
                parse_report.rejected,
                parse_report.field_errors,
            )

            # ------------------------------------------------------------------
            # Get or create station
            # ------------------------------------------------------------------
            station = await _get_or_create_station(session, settings)

            # ------------------------------------------------------------------
            # Create IngestionRun (status=running)  (Requirement 10.1, 10.2)
            # ------------------------------------------------------------------
            run = IngestionRun(
                source_id=_FIXTURE_SOURCE_ID,
                retrieval_time=retrieval_time,
                payload_size_bytes=payload_size,
                sha256_checksum=checksum,
                parser_version=PARSER_VERSION,
                normalizer_version=NORMALIZER_VERSION,
                accepted_count=None,
                rejected_count=parse_report.rejected,
                duplicate_count=None,
                status=IngestionStatus.RUNNING,
            )
            session.add(run)
            await session.flush()  # assign UUID

            # ------------------------------------------------------------------
            # Normalise and QC  (Requirements 4.*, 5.*, 7.*, 9.*)
            # ------------------------------------------------------------------
            normalized = [
                normalize_and_qc(
                    obs,
                    station,
                    ingestion_run_id=run.id,
                    data_mode="historical_replay",
                )
                for obs in parsed_observations
            ]

            # ------------------------------------------------------------------
            # Deduplicate  (Requirements 6.1, 6.3, 6.4)
            # ------------------------------------------------------------------
            dedup_result = await deduplicate(session, normalized)

            logger.info(
                "Deduplication complete: new=%d db_dupes=%d intra_run_dupes=%d",
                len(dedup_result.new_observations),
                dedup_result.duplicate_count,
                dedup_result.intra_run_duplicate_count,
            )

            total_duplicate_count = (
                dedup_result.duplicate_count
                + dedup_result.intra_run_duplicate_count
            )

            # ------------------------------------------------------------------
            # Bulk insert new NormalizedObservation rows
            # ------------------------------------------------------------------
            for obs in dedup_result.new_observations:
                session.add(obs)

            if dedup_result.new_observations:
                await session.flush()

            # ------------------------------------------------------------------
            # Finalise IngestionRun counts and status  (Requirement 10.3)
            # ------------------------------------------------------------------
            accepted_count = len(dedup_result.new_observations)
            run.accepted_count = accepted_count
            run.rejected_count = parse_report.rejected
            run.duplicate_count = total_duplicate_count
            run.status = IngestionStatus.COMPLETED

            await session.flush()

            # Capture scalar values before session closes
            run_id = run.id
            run_retrieval = run.retrieval_time
            run_checksum = run.sha256_checksum

        # session.begin() commits on __aexit__ when no exception raised.
        logger.info(
            "Transaction 1 committed: run_id=%s accepted=%d",
            run_id,
            accepted_count,
        )

    # ------------------------------------------------------------------
    # Transaction 2: aggregate computation  (Requirements 8.*, 9.2, 9.6)
    # ------------------------------------------------------------------
    async with session_factory() as session:
        async with session.begin():
            station_result = await session.execute(
                select(Station).where(
                    Station.provider_station_id == _FIXTURE_STATION_PROVIDER_ID
                )
            )
            agg_station = station_result.scalar_one()

            hourly = await compute_hourly_aggregates(
                session, agg_station.id, run_id
            )
            daily = await compute_daily_aggregates(
                session, agg_station.id, run_id
            )

        logger.info(
            "Transaction 2 committed: hourly=%d daily=%d",
            len(hourly),
            len(daily),
        )

    elapsed = time.monotonic() - start_time
    logger.info(
        "ingest_fixture complete: run_id=%s elapsed=%.2fs",
        run_id,
        elapsed,
    )

    # Return a lightweight result object with all scalars populated.
    return IngestionRun(
        id=run_id,
        source_id=_FIXTURE_SOURCE_ID,
        retrieval_time=run_retrieval,
        payload_size_bytes=payload_size,
        sha256_checksum=run_checksum,
        parser_version=PARSER_VERSION,
        normalizer_version=NORMALIZER_VERSION,
        accepted_count=accepted_count,
        rejected_count=parse_report.rejected,
        duplicate_count=total_duplicate_count,
        status=IngestionStatus.COMPLETED,
    )
