"""
Conduit Deduplicator.

Checks incoming ``NormalizedObservation`` objects against the database and
returns only the observations that do not already have a matching
``(station_id, valid_time_utc)`` row.

A single bulk query is used to avoid N+1 lookups (Requirements 6.1, 6.3, 6.4).

Requirements: 6.1, 6.3, 6.4
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence
import uuid

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conduit import NormalizedObservation

logger = logging.getLogger(__name__)


@dataclass
class DeduplicationResult:
    """Result of a deduplication pass.

    Attributes:
        new_observations: Observations not yet present in the database.
        duplicate_count: Number of incoming observations that were skipped
            because a matching ``(station_id, valid_time_utc)`` row already
            exists.
        intra_run_duplicate_count: Number of collisions detected *within*
            the incoming batch itself (same station + valid_time appearing
            more than once). Only the first occurrence is kept.
    """

    new_observations: list[NormalizedObservation]
    duplicate_count: int
    intra_run_duplicate_count: int


async def deduplicate(
    session: AsyncSession,
    observations: Sequence[NormalizedObservation],
) -> DeduplicationResult:
    """Filter ``observations`` to those not already stored in the database.

    Steps
    -----
    1. Deduplicate *within* the incoming batch first (Requirement 6.3).
       The first occurrence of each ``(station_id, valid_time_utc)`` pair is
       kept; subsequent occurrences are logged and counted.
    2. Bulk-query the database for any ``(station_id, valid_time_utc)`` pair
       that appears in the de-batched candidates (Requirement 6.1).
    3. Return a :class:`DeduplicationResult` containing only the truly new
       observations plus both duplicate counts.

    Parameters
    ----------
    session:
        An active ``AsyncSession``. The function issues a single SELECT; it
        does NOT flush or commit.
    observations:
        The list of ``NormalizedObservation`` instances produced by the
        normalizer for the current ingestion run.  These objects are not yet
        persisted.

    Returns
    -------
    DeduplicationResult
    """
    if not observations:
        return DeduplicationResult(
            new_observations=[],
            duplicate_count=0,
            intra_run_duplicate_count=0,
        )

    # ------------------------------------------------------------------
    # Step 1 — intra-batch deduplication (Requirement 6.3)
    # ------------------------------------------------------------------
    seen_keys: dict[tuple[uuid.UUID, datetime], NormalizedObservation] = {}
    intra_run_dupes = 0

    for obs in observations:
        key = (obs.station_id, obs.valid_time_utc)
        if key in seen_keys:
            existing = seen_keys[key]
            logger.warning(
                "Intra-run duplicate detected: station_id=%s valid_time_utc=%s — "
                "retaining first record. "
                "Conflicting raw values: first=%r second=%r",
                obs.station_id,
                obs.valid_time_utc,
                existing.temp_bmx_celsius,
                obs.temp_bmx_celsius,
            )
            intra_run_dupes += 1
        else:
            seen_keys[key] = obs

    # Unique candidates after intra-batch dedup, preserving insertion order.
    candidates: list[NormalizedObservation] = list(seen_keys.values())

    # ------------------------------------------------------------------
    # Step 2 — bulk database check (Requirements 6.1, 6.4)
    # ------------------------------------------------------------------
    # Build set of (station_id, valid_time_utc) tuples for the candidates.
    candidate_keys: set[tuple[uuid.UUID, datetime]] = {
        (obs.station_id, obs.valid_time_utc) for obs in candidates
    }

    # Single query: find which pairs already exist in the DB.
    stmt = select(
        NormalizedObservation.station_id,
        NormalizedObservation.valid_time_utc,
    ).where(
        tuple_(
            NormalizedObservation.station_id,
            NormalizedObservation.valid_time_utc,
        ).in_(candidate_keys)
    )

    result = await session.execute(stmt)
    existing_keys: set[tuple[uuid.UUID, datetime]] = {
        (row.station_id, row.valid_time_utc) for row in result
    }

    # ------------------------------------------------------------------
    # Step 3 — filter and count
    # ------------------------------------------------------------------
    new_observations: list[NormalizedObservation] = []
    db_duplicate_count = 0

    for obs in candidates:
        key = (obs.station_id, obs.valid_time_utc)
        if key in existing_keys:
            logger.debug(
                "Skipping duplicate: station_id=%s valid_time_utc=%s",
                obs.station_id,
                obs.valid_time_utc,
            )
            db_duplicate_count += 1
        else:
            new_observations.append(obs)

    return DeduplicationResult(
        new_observations=new_observations,
        duplicate_count=db_duplicate_count,
        intra_run_duplicate_count=intra_run_dupes,
    )
