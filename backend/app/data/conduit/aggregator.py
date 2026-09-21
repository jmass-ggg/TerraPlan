"""
Conduit Aggregator.

Computes hourly and daily aggregates from accepted ``NormalizedObservation``
rows for a given station and ingestion run.

Design rules (Requirements 8.1–8.8, 9.2, 9.6):
- Only observations with ``temperature_consensus_quality`` in (ACCEPTED,
  SINGLE_CHANNEL) contribute to temperature means. More broadly, a field
  contributes to an aggregate only when its individual quality flag is
  ACCEPTED (or SINGLE_CHANNEL for the consensus field).
- VPD mean is the mean of per-observation ``vpd_kpa`` values (requirement
  9.6), never recomputed from aggregated T and RH.
- Empty windows produce a record with null stat columns, observation_count=0,
  accepted_count=0, coverage_ratio=0.0, rather than being omitted (req 8.5).
- Time-weighted aggregation for irregular observation intervals (req 8.6).
- No fabricated values; null means no valid input (req 8.7).
- Each aggregate links to its source observation UUIDs via JSONB (req 8.8).

Transaction ownership: the Ingestion_Service calls these functions inside an
already-open AsyncSession; aggregates are added to the session but NOT
committed here.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conduit import (
    DailyAggregate,
    HourlyAggregate,
    NormalizedObservation,
    QualityFlag,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hour_boundary(dt: datetime) -> datetime:
    """Truncate *dt* to the UTC hour boundary."""
    return dt.replace(minute=0, second=0, microsecond=0, tzinfo=timezone.utc)


def _day_boundary(dt: datetime) -> datetime:
    """Truncate *dt* to the UTC midnight boundary."""
    return dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)


def _is_accepted(flag: QualityFlag) -> bool:
    """Return True when the quality flag counts as 'accepted' for aggregation.

    Temperature consensus with SINGLE_CHANNEL is allowed so that observations
    with only one working sensor still contribute.
    """
    return flag in (QualityFlag.ACCEPTED, QualityFlag.SINGLE_CHANNEL)


# ---------------------------------------------------------------------------
# Time-weighted mean
# ---------------------------------------------------------------------------

def _time_weighted_mean(
    pairs: list[tuple[datetime, float]],
) -> Optional[float]:
    """Compute a time-weighted mean from (timestamp, value) pairs.

    For N observations at times t_0 < t_1 < … < t_{N-1} the weight of
    observation i is proportional to the half-interval around it:

        w_i = 0.5 * (t_{i+1} − t_{i-1})   for interior points
        w_0 = 0.5 * (t_1 − t_0)            for the first point
        w_{N-1} = 0.5 * (t_{N-1} − t_{N-2}) for the last point

    For a single observation the unweighted value is returned.
    For an empty list None is returned.

    Requirements: 8.6
    """
    if not pairs:
        return None
    if len(pairs) == 1:
        return pairs[0][1]

    # Sort by time ascending so weight calculation is stable.
    sorted_pairs = sorted(pairs, key=lambda p: p[0])
    times = [p[0] for p in sorted_pairs]
    values = [p[1] for p in sorted_pairs]
    n = len(sorted_pairs)

    weights: list[float] = []
    for i in range(n):
        if i == 0:
            w = (times[1] - times[0]).total_seconds()
        elif i == n - 1:
            w = (times[n - 1] - times[n - 2]).total_seconds()
        else:
            w = (times[i + 1] - times[i - 1]).total_seconds()
        # Protect against zero-duration windows (duplicate timestamps after
        # dedup can't happen, but guard anyway).
        weights.append(max(w, 0.0))

    total_weight = sum(weights)
    if total_weight == 0.0:
        # All observations at the same instant — fall back to simple mean.
        return sum(values) / n

    return sum(v * w for v, w in zip(values, weights)) / total_weight


# ---------------------------------------------------------------------------
# Window accumulator
# ---------------------------------------------------------------------------

class _WindowStats:
    """Accumulates per-field data for a single aggregation window."""

    __slots__ = (
        "obs_ids",
        "temp_pairs",
        "humidity_pairs",
        "wind_spd_pairs",
        "vpd_values",
        "temp_min",
        "temp_max",
        "humidity_min",
        "humidity_max",
        "wind_spd_min",
        "wind_spd_max",
        "total_count",
        "accepted_count",
    )

    def __init__(self) -> None:
        self.obs_ids: list[str] = []
        self.temp_pairs: list[tuple[datetime, float]] = []
        self.humidity_pairs: list[tuple[datetime, float]] = []
        self.wind_spd_pairs: list[tuple[datetime, float]] = []
        self.vpd_values: list[float] = []
        # Running min/max — None until first accepted value.
        self.temp_min: Optional[float] = None
        self.temp_max: Optional[float] = None
        self.humidity_min: Optional[float] = None
        self.humidity_max: Optional[float] = None
        self.wind_spd_min: Optional[float] = None
        self.wind_spd_max: Optional[float] = None
        self.total_count: int = 0
        self.accepted_count: int = 0  # observations that contributed at least one field

    def add(self, obs: NormalizedObservation) -> None:
        """Ingest one observation into this window."""
        self.total_count += 1
        contributed = False
        ts = obs.valid_time_utc

        # Temperature consensus (Requirements 8.3, 8.6)
        if _is_accepted(obs.temperature_consensus_quality) and obs.temperature_consensus is not None:
            v = obs.temperature_consensus
            self.temp_pairs.append((ts, v))
            self.temp_min = v if self.temp_min is None else min(self.temp_min, v)
            self.temp_max = v if self.temp_max is None else max(self.temp_max, v)
            contributed = True

        # Humidity (Requirements 8.3, 8.6)
        if obs.humidity_sht_quality == QualityFlag.ACCEPTED and obs.humidity_sht_pct is not None:
            v = obs.humidity_sht_pct
            self.humidity_pairs.append((ts, v))
            self.humidity_min = v if self.humidity_min is None else min(self.humidity_min, v)
            self.humidity_max = v if self.humidity_max is None else max(self.humidity_max, v)
            contributed = True

        # Wind speed (Requirements 8.3, 8.6)
        if obs.wind_spd_quality == QualityFlag.ACCEPTED and obs.wind_spd_ms is not None:
            v = obs.wind_spd_ms
            self.wind_spd_pairs.append((ts, v))
            self.wind_spd_min = v if self.wind_spd_min is None else min(self.wind_spd_min, v)
            self.wind_spd_max = v if self.wind_spd_max is None else max(self.wind_spd_max, v)
            contributed = True

        # VPD — mean of per-observation values (Requirements 9.2, 9.6)
        if obs.vpd_quality == QualityFlag.ACCEPTED and obs.vpd_kpa is not None:
            self.vpd_values.append(obs.vpd_kpa)
            contributed = True

        if contributed:
            self.accepted_count += 1

        if obs.id is not None:
            self.obs_ids.append(str(obs.id))

    def coverage_ratio(self) -> float:
        """Coverage = accepted_count / max(total_count, 1).

        Requirements: 8.4, 8.5
        """
        return self.accepted_count / max(self.total_count, 1)

    def temp_mean(self) -> Optional[float]:
        return _time_weighted_mean(self.temp_pairs)

    def humidity_mean(self) -> Optional[float]:
        return _time_weighted_mean(self.humidity_pairs)

    def wind_spd_mean(self) -> Optional[float]:
        return _time_weighted_mean(self.wind_spd_pairs)

    def vpd_mean(self) -> Optional[float]:
        if not self.vpd_values:
            return None
        return sum(self.vpd_values) / len(self.vpd_values)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def compute_hourly_aggregates(
    session: AsyncSession,
    station_id: uuid.UUID,
    run_id: uuid.UUID,
) -> list[HourlyAggregate]:
    """Compute (or recompute) hourly aggregates for the given station.

    Loads all ``NormalizedObservation`` rows for the station, groups them
    by UTC hour, computes stats over accepted observations, and upserts
    ``HourlyAggregate`` rows.

    Only ACCEPTED observations contribute to stats (requirement 8.1).
    Empty windows for hours that have observations produce null-stat records
    rather than being skipped (requirement 8.5).

    Returns the list of ``HourlyAggregate`` objects added to the session.
    Requirements: 8.1, 8.3–8.8, 9.2, 9.6
    """
    stmt = (
        select(NormalizedObservation)
        .where(NormalizedObservation.station_id == station_id)
        .order_by(NormalizedObservation.valid_time_utc)
    )
    result = await session.execute(stmt)
    observations: list[NormalizedObservation] = list(result.scalars().all())

    if not observations:
        return []

    # Group observations by UTC hour boundary.
    windows: dict[datetime, _WindowStats] = defaultdict(_WindowStats)
    data_mode = observations[0].data_mode

    for obs in observations:
        bucket = _hour_boundary(obs.valid_time_utc)
        windows[bucket].add(obs)

    # Delete existing hourly aggregates for this station so we can recompute.
    await session.execute(
        delete(HourlyAggregate).where(HourlyAggregate.station_id == station_id)
    )

    aggregates: list[HourlyAggregate] = []
    for window_start, stats in sorted(windows.items()):
        agg = HourlyAggregate(
            station_id=station_id,
            window_start_utc=window_start,
            data_mode=data_mode,
            # Temperature stats
            temp_mean_celsius=stats.temp_mean(),
            temp_min_celsius=stats.temp_min,
            temp_max_celsius=stats.temp_max,
            # Humidity stats
            humidity_mean_pct=stats.humidity_mean(),
            humidity_min_pct=stats.humidity_min,
            humidity_max_pct=stats.humidity_max,
            # Wind stats
            wind_spd_mean_ms=stats.wind_spd_mean(),
            wind_spd_min_ms=stats.wind_spd_min,
            wind_spd_max_ms=stats.wind_spd_max,
            # VPD mean from per-observation VPD (req 9.6)
            vpd_mean_kpa=stats.vpd_mean(),
            # Coverage
            observation_count=stats.total_count,
            accepted_count=stats.accepted_count,
            coverage_ratio=stats.coverage_ratio(),
            # Source observation links (req 8.8)
            source_observation_ids=stats.obs_ids,
        )
        session.add(agg)
        aggregates.append(agg)

    logger.info(
        "Computed %d hourly aggregates for station %s (run %s)",
        len(aggregates),
        station_id,
        run_id,
    )
    return aggregates


async def compute_daily_aggregates(
    session: AsyncSession,
    station_id: uuid.UUID,
    run_id: uuid.UUID,
) -> list[DailyAggregate]:
    """Compute (or recompute) daily aggregates for the given station.

    Same semantics as :func:`compute_hourly_aggregates` but grouping by UTC
    midnight boundaries.

    Requirements: 8.2–8.8, 9.2, 9.6
    """
    stmt = (
        select(NormalizedObservation)
        .where(NormalizedObservation.station_id == station_id)
        .order_by(NormalizedObservation.valid_time_utc)
    )
    result = await session.execute(stmt)
    observations: list[NormalizedObservation] = list(result.scalars().all())

    if not observations:
        return []

    # Group observations by UTC midnight boundary.
    windows: dict[datetime, _WindowStats] = defaultdict(_WindowStats)
    data_mode = observations[0].data_mode

    for obs in observations:
        bucket = _day_boundary(obs.valid_time_utc)
        windows[bucket].add(obs)

    # Delete existing daily aggregates for this station so we can recompute.
    await session.execute(
        delete(DailyAggregate).where(DailyAggregate.station_id == station_id)
    )

    aggregates: list[DailyAggregate] = []
    for window_start, stats in sorted(windows.items()):
        agg = DailyAggregate(
            station_id=station_id,
            window_start_utc=window_start,
            data_mode=data_mode,
            # Temperature stats
            temp_mean_celsius=stats.temp_mean(),
            temp_min_celsius=stats.temp_min,
            temp_max_celsius=stats.temp_max,
            # Humidity stats
            humidity_mean_pct=stats.humidity_mean(),
            humidity_min_pct=stats.humidity_min,
            humidity_max_pct=stats.humidity_max,
            # Wind stats
            wind_spd_mean_ms=stats.wind_spd_mean(),
            wind_spd_min_ms=stats.wind_spd_min,
            wind_spd_max_ms=stats.wind_spd_max,
            # VPD mean from per-observation VPD (req 9.6)
            vpd_mean_kpa=stats.vpd_mean(),
            # Coverage
            observation_count=stats.total_count,
            accepted_count=stats.accepted_count,
            coverage_ratio=stats.coverage_ratio(),
            # Source observation links (req 8.8)
            source_observation_ids=stats.obs_ids,
        )
        session.add(agg)
        aggregates.append(agg)

    logger.info(
        "Computed %d daily aggregates for station %s (run %s)",
        len(aggregates),
        station_id,
        run_id,
    )
    return aggregates
