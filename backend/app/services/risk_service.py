"""
Risk service for the Farm Risk Center.

Orchestrates farm ownership checks, snapshot loading, risk engine calls,
and action completion persistence for the /risks and /actions routes.

Requirements: 1.1, 1.2, 7.4, 7.5, 8.1
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from geoalchemy2.shape import to_shape
from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import FarmValidationError, NotFoundError
from app.core.security import Principal
from app.domain.risk_engine import (
    DROUGHT_HIGH_RATIO,
    DROUGHT_MEDIUM_RATIO,
    FLOOD_HIGH_SLOPE_PCT,
    FLOOD_MEDIUM_SLOPE_PCT,
    HEAT_HIGH_TEMP,
    HEAT_MEDIUM_TEMP,
    HEAVY_RAIN_HIGH_MM,
    HEAVY_RAIN_MEDIUM_MM,
    LEVEL_HIGH,
    LEVEL_LOW,
    LEVEL_MEDIUM,
    LEVEL_UNKNOWN,
    WIND_HIGH_MS,
    WIND_MEDIUM_MS,
    RiskResponse,
    _clamp,
    assess_all,
)
from app.domain.risk_rules import ACTION_RULES
from app.domain.snapshot_context import context_from_demonstration, context_for_risks
from app.models.actions import ActionCompletion
from app.models.farm import Farm
from app.models.snapshot import AnalysisSnapshot
from app.repositories.farm import FarmRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Timeline data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TimelineHazard:
    """Single hazard assessment for timeline."""

    index: int | None
    level: str


@dataclass(frozen=True)
class TimelinePoint:
    """All hazard assessments for a single date."""

    date: str
    drought: TimelineHazard
    heat: TimelineHazard
    heavy_rainfall: TimelineHazard
    flood_exposure: TimelineHazard
    wind: TimelineHazard


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

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


async def _load_action_completions(
    session: AsyncSession,
    farm_id: UUID,
) -> dict[str, ActionCompletion]:
    """Return a dict of action_id → ActionCompletion for this farm."""
    result = await session.execute(
        select(ActionCompletion).where(ActionCompletion.farm_id == farm_id)
    )
    rows = result.scalars().all()
    return {row.action_id: row for row in rows}


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

async def get_risks(
    session: AsyncSession,
    principal: Principal,
    farm_id: UUID,
    settings: Settings,
) -> RiskResponse:
    """
    Load the farm, build a SnapshotContext, assess all 5 hazards, and overlay
    action completion status on each assessment's action list.

    1. Ownership check via FarmRepository (raises NotFoundError → 404).
    2. Load the latest AnalysisSnapshot or fall back to demonstration profile.
    3. Build SnapshotContext and call assess_all().
    4. Load ActionCompletion records for this farm.
    5. The returned RiskResponse contains action completion state embedded
       in each action's overlay (the API layer converts this to the response).

    Requirements: 1.1, 1.2, 8.1
    """
    # 1. Ownership-scoped farm fetch
    repo = FarmRepository(session, principal.user_id)
    farm: Farm = await repo.get_by_id(farm_id)

    centroid = to_shape(farm.current_geometry.centroid)

    # 2. Attempt to load the latest snapshot
    snapshot = await _load_latest_snapshot(
        session, farm.id, farm.current_geometry_revision
    )

    # 3. Build SnapshotContext
    if snapshot is not None:
        context = context_for_risks(snapshot)
        logger.debug(
            "Built snapshot context for risk assessment: farm=%s snapshot=%s data_mode=%s",
            farm_id,
            snapshot.id,
            snapshot.data_mode,
        )
    else:
        if settings.data_mode.value != "demonstration":
            raise NotFoundError("No analysis snapshot available. Run farm analysis first.")
        # Explicit demonstration mode only
        context = context_from_demonstration(
            latitude=centroid.y,
            longitude=centroid.x,
            planting_month=datetime.now(timezone.utc).month,
        )
        logger.debug(
            "No snapshot for farm %s — using demonstration fallback for risk assessment.",
            farm_id,
        )

    # 4. Call the risk engine
    risk_response = assess_all(context, str(farm.id))

    return risk_response


async def complete_action(
    session: AsyncSession,
    principal: Principal,
    farm_id: UUID,
    action_id: str,
) -> ActionCompletion:
    """
    Mark an action as complete for the given farm.

    1. Ownership check via FarmRepository.
    2. Validate action_id exists in ACTION_RULES.
    3. Upsert ActionCompletion record (idempotent — retains original timestamp).
    4. Return the completion record.

    Requirements: 7.4, 7.5
    """
    # 1. Ownership check
    repo = FarmRepository(session, principal.user_id)
    await repo.get_by_id(farm_id)  # raises NotFoundError → 404 if not found/not owned

    # 2. Validate action_id
    known_ids = {rule.id for rule in ACTION_RULES}
    if action_id not in known_ids:
        raise FarmValidationError(
            field="path.action_id",
            detail_code="INVALID_VALUE",
            message=f"Unknown action id: {action_id!r}",
        )

    # 3. Upsert — on conflict (farm_id, action_id) do nothing so the original
    #    completed_at timestamp is preserved (idempotent, Requirement 7.5).
    now = datetime.now(tz=timezone.utc)
    stmt = (
        pg_insert(ActionCompletion)
        .values(
            farm_id=farm_id,
            action_id=action_id,
            completed_at=now,
        )
        .on_conflict_do_nothing(
            constraint="uq_action_completions_farm_action",
        )
    )
    await session.execute(stmt)
    await session.flush()

    # 4. Retrieve the persisted record (may be the existing one)
    result = await session.execute(
        select(ActionCompletion).where(
            ActionCompletion.farm_id == farm_id,
            ActionCompletion.action_id == action_id,
        )
    )
    completion = result.scalar_one()

    await session.commit()
    return completion


async def get_risk_timeline(
    session: AsyncSession,
    principal: Principal,
    farm_id: UUID,
    days: int,
    settings: Settings,
) -> tuple[list[TimelinePoint], str | None]:
    """
    Calculate 7-day risk timeline from daily forecast evidence.

    Returns (timeline_points, snapshot_id).
    Timeline points are ordered by date, oldest first.
    
    Requirements: Uses existing risk engine thresholds for consistency.
    """
    # 1. Ownership check
    repo = FarmRepository(session, principal.user_id)
    farm: Farm = await repo.get_by_id(farm_id)

    # 2. Load latest snapshot
    snapshot = await _load_latest_snapshot(
        session, farm.id, farm.current_geometry_revision
    )

    if snapshot is None:
        raise NotFoundError("No analysis snapshot available. Run farm analysis first.")

    weather = snapshot.weather or {}
    daily = weather.get("daily") or {}
    daily_units = weather.get("daily_units") or {}

    # Extract daily arrays
    temp_min_list = daily.get("temperature_2m_min") or []
    temp_max_list = daily.get("temperature_2m_max") or []
    temp_mean_list = daily.get("temperature_2m_mean") or []
    precip_list = daily.get("precipitation_sum") or []
    wind_list = daily.get("wind_speed_10m_max") or []

    # Get terrain slope for flood assessment
    terrain = snapshot.terrain or {}
    mean_slope_deg = terrain.get("mean_slope_deg", {}).get("value")
    slope_pct = None
    if mean_slope_deg is not None:
        slope_pct = 100 * math.tan(math.radians(mean_slope_deg))

    # Get baseline rainfall for drought
    climate_baseline = snapshot.climate_baseline or {}
    monthly_precip = (climate_baseline.get("all_monthly_means") or {}).get("precipitation_sum") or {}

    # Limit to available forecast days
    available_days = min(
        days,
        len(temp_mean_list),
        len(precip_list),
        len(wind_list)
    )

    if available_days == 0:
        raise NotFoundError("No daily forecast data available in snapshot.")

    # Build timeline points
    points: list[TimelinePoint] = []
    valid_date = snapshot.valid_time_utc.date()

    for day_offset in range(available_days):
        forecast_date = valid_date + timedelta(days=day_offset)
        date_str = forecast_date.isoformat()

        # Extract daily values
        temp_mean = temp_mean_list[day_offset] if day_offset < len(temp_mean_list) else None
        precip_day = precip_list[day_offset] if day_offset < len(precip_list) else None
        wind_max = wind_list[day_offset] if day_offset < len(wind_list) else None

        # Convert wind from km/h to m/s if needed
        wind_unit = daily_units.get("wind_speed_10m_max", "m/s")
        if wind_max is not None and wind_unit == "km/h":
            wind_max = wind_max / 3.6

        # Calculate 7-day rainfall accumulation ending on this date
        rain_7d = None
        if day_offset >= 6 and len(precip_list) > day_offset:
            window = precip_list[day_offset - 6:day_offset + 1]
            if len(window) == 7 and all(v is not None for v in window):
                rain_7d = sum(window)

        # Calculate baseline for same 7-day period
        baseline_7d = None
        if rain_7d is not None:
            baseline_7d = 0.0
            import calendar
            for offset in range(-6, 1):
                check_date = forecast_date + timedelta(days=offset)
                month_str = str(check_date.month)
                monthly_value = monthly_precip.get(month_str)
                if monthly_value is None:
                    baseline_7d = None
                    break
                days_in_month = calendar.monthrange(check_date.year, check_date.month)[1]
                baseline_7d += monthly_value / days_in_month

        # Assess each hazard
        drought = _assess_drought_timeline(rain_7d, baseline_7d)
        heat = _assess_heat_timeline(temp_mean)
        heavy_rainfall = _assess_heavy_rainfall_timeline(rain_7d)
        flood = _assess_flood_timeline(slope_pct, heavy_rainfall.level)
        wind = _assess_wind_timeline(wind_max)

        points.append(TimelinePoint(
            date=date_str,
            drought=drought,
            heat=heat,
            heavy_rainfall=heavy_rainfall,
            flood_exposure=flood,
            wind=wind,
        ))

    return points, str(snapshot.id) if snapshot else None


def _assess_drought_timeline(rain_7d: float | None, baseline_7d: float | None) -> TimelineHazard:
    """Assess drought from 7-day rainfall vs baseline, reusing existing thresholds."""
    if rain_7d is None or baseline_7d is None or baseline_7d <= 0:
        return TimelineHazard(index=None, level=LEVEL_UNKNOWN)

    ratio = rain_7d / baseline_7d
    index = _clamp((1.0 - ratio) * 100, 0, 100)

    if ratio < DROUGHT_HIGH_RATIO:
        level = LEVEL_HIGH
    elif ratio < DROUGHT_MEDIUM_RATIO:
        level = LEVEL_MEDIUM
    else:
        level = LEVEL_LOW

    return TimelineHazard(index=index, level=level)


def _assess_heat_timeline(temp_mean: float | None) -> TimelineHazard:
    """Assess heat from daily mean temperature, reusing existing thresholds."""
    if temp_mean is None:
        return TimelineHazard(index=None, level=LEVEL_UNKNOWN)

    index = _clamp((temp_mean - 20.0) / 15.0 * 100.0, 0, 100)

    if temp_mean > HEAT_HIGH_TEMP:
        level = LEVEL_HIGH
    elif temp_mean > HEAT_MEDIUM_TEMP:
        level = LEVEL_MEDIUM
    else:
        level = LEVEL_LOW

    return TimelineHazard(index=index, level=level)


def _assess_heavy_rainfall_timeline(rain_7d: float | None) -> TimelineHazard:
    """Assess heavy rainfall from 7-day accumulation, reusing existing thresholds."""
    if rain_7d is None:
        return TimelineHazard(index=None, level=LEVEL_UNKNOWN)

    index = _clamp(rain_7d / HEAVY_RAIN_HIGH_MM * 100.0, 0, 100)

    if rain_7d > HEAVY_RAIN_HIGH_MM:
        level = LEVEL_HIGH
    elif rain_7d > HEAVY_RAIN_MEDIUM_MM:
        level = LEVEL_MEDIUM
    else:
        level = LEVEL_LOW

    return TimelineHazard(index=index, level=level)


def _assess_flood_timeline(slope_pct: float | None, heavy_rain_level: str) -> TimelineHazard:
    """Assess flood from slope and heavy rainfall level, reusing existing logic."""
    if slope_pct is None:
        return TimelineHazard(index=None, level=LEVEL_UNKNOWN)

    if heavy_rain_level == LEVEL_LOW:
        index = _clamp(slope_pct * 2.0, 0, 30)
        return TimelineHazard(index=index, level=LEVEL_LOW)

    if slope_pct < FLOOD_HIGH_SLOPE_PCT and heavy_rain_level in (LEVEL_MEDIUM, LEVEL_HIGH):
        return TimelineHazard(index=85, level=LEVEL_HIGH)
    elif slope_pct < FLOOD_MEDIUM_SLOPE_PCT and heavy_rain_level == LEVEL_HIGH:
        return TimelineHazard(index=60, level=LEVEL_MEDIUM)
    else:
        index = _clamp(slope_pct * 3.0, 0, 40)
        return TimelineHazard(index=index, level=LEVEL_LOW)


def _assess_wind_timeline(wind_max: float | None) -> TimelineHazard:
    """Assess wind from maximum wind speed, reusing existing thresholds."""
    if wind_max is None:
        return TimelineHazard(index=None, level=LEVEL_UNKNOWN)

    index = _clamp(wind_max / 20.0 * 100.0, 0, 100)

    if wind_max > WIND_HIGH_MS:
        level = LEVEL_HIGH
    elif wind_max > WIND_MEDIUM_MS:
        level = LEVEL_MEDIUM
    else:
        level = LEVEL_LOW

    return TimelineHazard(index=index, level=level)
