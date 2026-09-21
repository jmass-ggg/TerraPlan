"""
Planner service for the Annual Planner feature (Phase 8).

Manages Plan_Entry persistence, overlap validation, harvest date derivation,
12-month crop recommendations, and Change_Proposal acceptance.

Requirements: 1.1, 1.2, 2.1–2.6, 3.1–3.4, 4.3, 4.4
"""

from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Callable
from uuid import UUID

from geoalchemy2.shape import to_shape
from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import FarmValidationError, NotFoundError
from app.core.security import Principal
from app.domain.crop_engine import ENGINE_VERSION, rank_all, score
from app.domain.crop_register import CROP_REGISTER, CropRequirements
from app.domain.snapshot_context import SnapshotContext
from app.domain.snapshot_context import context_from_demonstration, context_from_snapshot, with_irrigation
from app.core.config import Settings
from app.models.farm import Farm
from app.models.plan import ChangeProposal, PlanEntry, ProposalStatus
from app.models.snapshot import AnalysisSnapshot
from app.repositories.farm import FarmRepository
from app.services.planner_optimizer import (
    CropCycleCandidate,
    CropPlanOptimizer,
    deterministic_fallback,
)

logger = logging.getLogger(__name__)


async def _generate_ai_explanation(
    crop_name: str,
    context: SnapshotContext,
    month: int,
    suitability_index: int,
    limiting_factor: str | None,
    previous_crop: str | None,
    rotation_effect: str,
    farm_id: str,
) -> str:
    """
    Generate AI explanation for annual plan recommendation.
    
    Falls back to simple rotation reason on any failure.
    """
    try:
        from app.services.ai.annual_plan_explanation_service import (
            get_annual_plan_explanation_service,
        )
        
        # Find crop in register
        crop = next(
            (c for c in CROP_REGISTER if c.name == crop_name),
            None,
        )
        if crop is None:
            return "Strong seasonal match"
        
        service = get_annual_plan_explanation_service()
        explanation = await service.generate(
            crop=crop,
            context=context,
            month=month,
            month_name=calendar.month_name[month],
            suitability_index=suitability_index,
            limiting_factor=limiting_factor,
            previous_crop=previous_crop,
            rotation_effect=rotation_effect,
            farm_id=farm_id,
        )
        return explanation.text
    except Exception as exc:
        logger.warning(
            "Failed to generate AI explanation for %s in month %d: %s",
            crop_name, month, exc,
        )
        # Fallback to simple rotation reason
        if previous_crop and rotation_effect == "preferred":
            return f"Good next crop after {previous_crop}"
        elif rotation_effect == "same-crop penalty":
            return "Strong seasonal match despite repeating the previous crop"
        elif rotation_effect == "avoid":
            return "Best available match for this planting window"
        return "Strong seasonal match"


# ---------------------------------------------------------------------------
# Response dataclasses (lightweight, no ORM coupling)
# ---------------------------------------------------------------------------


@dataclass
class MonthRecommendation:
    """Top-3 crop recommendations for a single calendar month."""

    month: int
    month_name: str
    data_mode: str
    snapshot_id: str | None
    recommendations: list[dict]  # top-3 ranked results as dicts


@dataclass
class AnnualPlanResponse:
    """All 12 monthly recommendations plus saved entries and proposals."""

    farm_id: str
    year: int
    months: list[MonthRecommendation]  # exactly 12
    entries: list[PlanEntry]
    proposals: list[ChangeProposal]
    timeline: list["TimelineItem"] = field(default_factory=list)
    perennial_opportunities: list["PerennialOpportunity"] = field(default_factory=list)
    solver_status: str = "not_run"
    fallback_used: bool = False
    explanation: str = "FarmTwin balanced crop conditions, growing time, your calendar and crop rotation."


@dataclass
class PlanEntryCreate:
    """Input data for creating a new PlanEntry."""

    crop_name: str
    planting_date: date
    cultivation_mode: str
    irrigation_mm: float | None
    area_ha: float
    field_name: str | None = None


@dataclass
class PlanEntryUpdate:
    """Input data for updating an existing PlanEntry."""

    planting_date: date | None = None
    cultivation_mode: str | None = None
    irrigation_mm: float | None = None
    area_ha: float | None = None
    field_name: str | None = None
    expected_revision: int | None = None


@dataclass(frozen=True)
class TimelineItem:
    month: int
    month_name: str
    crop_name: str | None
    stage: str
    action: str
    season_id: str | None
    suitability_index: int | None = None
    planning_score: int | None = None
    plant_month: int | None = None
    harvest_month: int | None = None
    duration_months: int | None = None
    previous_crop: str | None = None
    rotation_effect: str | None = None
    reason: str | None = None
    limiting_factor: str | None = None
    continues_next_year: bool = False
    data_mode: str = "demonstration"
    snapshot_id: str | None = None
    saved: bool = False


@dataclass(frozen=True)
class PerennialOpportunity:
    crop_name: str
    suitability_index: int
    label: str
    limiting_factor: str
    reason: str


PREFERRED_ROTATION_BONUS = 10
AVOID_ROTATION_PENALTY = -15
SAME_CROP_PENALTY = -20
SAME_FAMILY_PENALTY = -8


def _stage_for_month(offset: int, duration: int) -> str:
    """Return one deterministic planning stage for a zero-based crop month."""
    if offset == 0:
        return "planting"
    if offset == duration - 1:
        return "harvest"
    if duration == 3:
        return "growing"
    if duration == 4:
        return "growing" if offset == 1 else "maturing"
    if duration >= 5 and offset == duration - 2:
        return "maturing"
    if duration >= 5 and offset == max(2, duration // 2):
        return "flowering"
    return "growing"


def _rotation_adjustment(
    crop: CropRequirements,
    previous: CropRequirements | None,
) -> tuple[int, str]:
    if previous is None:
        return 0, "neutral"
    if crop.name == previous.name:
        return SAME_CROP_PENALTY, "same-crop penalty"
    if previous.name in crop.avoid_after:
        return AVOID_ROTATION_PENALTY, "avoid"
    if previous.name in crop.preferred_after:
        return PREFERRED_ROTATION_BONUS, "preferred"
    if crop.family != "unknown" and crop.family == previous.family:
        return SAME_FAMILY_PENALTY, "same-family penalty"
    return 0, "neutral"


def _build_annual_sequence(
    crops: tuple[CropRequirements, ...],
    context_for: Callable[[int, CropRequirements], SnapshotContext],
) -> tuple[list[TimelineItem], list[PerennialOpportunity]]:
    """Build one occupancy-aware January–December field sequence."""
    annuals = tuple(crop for crop in crops if crop.crop_type == "annual")
    perennials = tuple(crop for crop in crops if crop.crop_type == "perennial")
    timeline: list[TimelineItem] = []
    previous: CropRequirements | None = None
    season_counts: dict[str, int] = {}
    month = 1

    while month <= 12 and annuals:
        candidates = []
        for crop in annuals:
            result = score(crop, context_for(month, crop))
            if result.suitability_index is None or result.hard_exclusion:
                continue
            adjustment, effect = _rotation_adjustment(crop, previous)
            planning_score = max(0, min(100, result.suitability_index + adjustment))
            candidates.append((planning_score, result.suitability_index, crop.name, crop, result, effect))
        if not candidates:
            break

        _, suitability, _, crop, result, effect = max(candidates, key=lambda item: (item[0], item[1], item[2]))
        adjustment, _ = _rotation_adjustment(crop, previous)
        planning_score = max(0, min(100, suitability + adjustment))
        season_counts[crop.name] = season_counts.get(crop.name, 0) + 1
        season_id = f"{crop.name.lower().replace(' ', '-')}-{season_counts[crop.name]}"
        harvest_month = month + crop.duration_months - 1
        continues = harvest_month > 12
        if effect == "preferred" and previous:
            rotation_sentence = f" It provides a preferred rotation after {previous.name}."
        elif effect == "avoid" and previous:
            rotation_sentence = f" Rotation after {previous.name} carries a planning penalty."
        elif effect == "same-crop penalty":
            rotation_sentence = " Repeating the same crop carries a strong rotation penalty."
        elif effect == "same-family penalty" and previous:
            rotation_sentence = f" Following {previous.name} in the same family carries a rotation penalty."
        else:
            rotation_sentence = ""
        explanation = f"{crop.name} is suitable for {calendar.month_name[month]} conditions.{rotation_sentence}"
        context = context_for(month, crop)

        for offset in range(crop.duration_months):
            occupied_month = month + offset
            if occupied_month > 12:
                break
            stage = _stage_for_month(offset, crop.duration_months)
            timeline.append(TimelineItem(
                month=occupied_month,
                month_name=calendar.month_name[occupied_month],
                crop_name=crop.name,
                stage=stage,
                action="plant" if stage == "planting" else "harvest" if stage == "harvest" else "continue",
                season_id=season_id,
                suitability_index=suitability if stage == "planting" else None,
                planning_score=planning_score if stage == "planting" else None,
                plant_month=month,
                harvest_month=harvest_month if harvest_month <= 12 else None,
                duration_months=crop.duration_months,
                previous_crop=previous.name if previous else None,
                rotation_effect=effect,
                reason=explanation if stage == "planting" else None,
                limiting_factor=result.limiting_factor if stage == "planting" else None,
                continues_next_year=continues,
                data_mode=context.data_mode,
                snapshot_id=context.snapshot_id,
            ))

        month += crop.duration_months
        for _ in range(crop.recovery_months):
            if month > 12:
                break
            timeline.append(TimelineItem(
                month=month,
                month_name=calendar.month_name[month],
                crop_name=None,
                stage="recovery",
                action="recover",
                season_id=None,
            ))
            month += 1
        previous = crop

    opportunities = []
    for crop in perennials:
        result = score(crop, context_for(1, crop))
        if result.suitability_index is not None and not result.hard_exclusion:
            opportunities.append(PerennialOpportunity(
                crop_name=crop.name,
                suitability_index=result.suitability_index,
                label=result.label,
                limiting_factor=result.limiting_factor,
                reason=result.reason,
            ))
    opportunities.sort(key=lambda item: (-item.suitability_index, item.crop_name))
    return timeline, opportunities


def _entry_occupied_months(entry: PlanEntry, year: int) -> set[int]:
    """Return calendar months intersected by a saved half-open entry period."""
    occupied: set[int] = set()
    for month in range(1, 13):
        month_start = date(year, month, 1)
        next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        if entry.planting_date < next_month and entry.harvest_date > month_start:
            occupied.add(month)
    return occupied


def _build_cycle_candidates(
    crops: tuple[CropRequirements, ...],
    context_for: Callable[[int, CropRequirements], SnapshotContext],
    *,
    earliest_start_month: int,
    blocked_months: set[int],
) -> tuple[list[CropCycleCandidate], list[PerennialOpportunity]]:
    """Score feasible whole-crop cycles; crop_engine remains the score authority."""
    candidates: list[CropCycleCandidate] = []
    perennials: list[PerennialOpportunity] = []

    for crop in crops:
        if crop.crop_type == "perennial":
            result = score(crop, context_for(max(1, earliest_start_month), crop))
            if result.suitability_index is not None and not result.hard_exclusion:
                perennials.append(PerennialOpportunity(
                    crop_name=crop.name,
                    suitability_index=result.suitability_index,
                    label=result.label,
                    limiting_factor=result.limiting_factor,
                    reason=result.reason,
                ))
            continue

        for start_month in range(max(1, earliest_start_month), 13):
            result = score(crop, context_for(start_month, crop))
            if result.suitability_index is None or result.hard_exclusion:
                continue
            occupied = tuple(
                month
                for month in range(start_month, start_month + crop.duration_months)
                if month <= 12
            )
            recovery_start = start_month + crop.duration_months
            recovery = tuple(
                month
                for month in range(recovery_start, recovery_start + crop.recovery_months)
                if month <= 12
            )
            if blocked_months.intersection((*occupied, *recovery)):
                continue
            raw_harvest = start_month + crop.duration_months - 1
            context = context_for(start_month, crop)
            candidates.append(CropCycleCandidate(
                crop_name=crop.name,
                category=crop.category,
                family=crop.family,
                start_month=start_month,
                duration_months=crop.duration_months,
                occupied_months=occupied,
                recovery_months=recovery,
                harvest_month=raw_harvest if raw_harvest <= 12 else None,
                suitability_index=result.suitability_index,
                limiting_factor=result.limiting_factor,
                reason=result.reason,
                data_mode=context.data_mode,
                snapshot_id=context.snapshot_id,
                continues_next_year=raw_harvest > 12,
                preferred_after=crop.preferred_after,
                avoid_after=crop.avoid_after,
            ))

    perennials.sort(key=lambda item: (-item.suitability_index, item.crop_name))
    return candidates, perennials


def _rotation_reason(
    cycle: CropCycleCandidate,
    previous: CropCycleCandidate | None,
) -> tuple[str, str]:
    """
    Return a simple rotation-based reason and rotation effect code.
    
    Note: This is now a fallback. The main annual plan generation uses
    AI-generated explanations via _generate_ai_explanation.
    """
    if previous is None:
        return "Strong seasonal match", "neutral"
    if previous.crop_name in cycle.preferred_after:
        return f"Good next crop after {previous.crop_name}", "preferred"
    if previous.crop_name == cycle.crop_name:
        return "Strong seasonal match despite repeating the previous crop", "same-crop penalty"
    if previous.crop_name in cycle.avoid_after:
        return "Best available match for this planting window", "avoid"
    if previous.family == cycle.family and cycle.family != "unknown":
        return "Strong seasonal match", "same-family penalty"
    return "Good crop variety with a strong seasonal match", "diverse"


async def _cycles_to_timeline_with_ai(
    cycles: tuple[CropCycleCandidate, ...],
    saved_entries: list[PlanEntry],
    year: int,
    farm_id: str,
    context_for: Callable[[int, CropRequirements], SnapshotContext],
) -> list[TimelineItem]:
    """
    Convert optimized cycles plus fixed saved occupancy to the public timeline.
    
    Generates AI explanations for each planting event using actual environmental context.
    """
    by_month: dict[int, TimelineItem] = {}
    for entry in saved_entries:
        months = sorted(_entry_occupied_months(entry, year))
        if not months:
            continue
        crop = next((item for item in CROP_REGISTER if item.name.casefold() == entry.crop_name.casefold()), None)
        duration = crop.duration_months if crop else len(months)
        for offset, month in enumerate(months):
            is_planting = entry.planting_date.year == year and entry.planting_date.month == month
            is_harvest = entry.harvest_date.year == year and entry.harvest_date.month == month
            stage = "planting" if is_planting else "harvest" if is_harvest else "growing"
            by_month.setdefault(month, TimelineItem(
                month=month,
                month_name=calendar.month_name[month],
                crop_name=entry.crop_name,
                stage=stage,
                action="plant" if is_planting else "harvest" if is_harvest else "continue",
                season_id=f"saved-{entry.id}",
                suitability_index=entry.suitability_index if is_planting or month == months[0] else None,
                planning_score=entry.suitability_index if is_planting or month == months[0] else None,
                plant_month=entry.planting_date.month if entry.planting_date.year == year else None,
                harvest_month=entry.harvest_date.month if entry.harvest_date.year == year else None,
                duration_months=duration,
                reason="Saved in your farm calendar" if is_planting or month == months[0] else None,
                data_mode=entry.data_mode,
                snapshot_id=str(entry.snapshot_id) if entry.snapshot_id else None,
                saved=True,
            ))

    previous: CropCycleCandidate | None = None
    counts: dict[str, int] = {}
    for cycle in sorted(cycles, key=lambda item: (item.start_month, item.crop_name)):
        counts[cycle.crop_name] = counts.get(cycle.crop_name, 0) + 1
        season_id = f"{cycle.crop_name.lower().replace(' ', '-')}-{counts[cycle.crop_name]}"
        _, effect = _rotation_reason(cycle, previous)
        adjustment = {
            "preferred": PREFERRED_ROTATION_BONUS,
            "same-crop penalty": SAME_CROP_PENALTY,
            "avoid": AVOID_ROTATION_PENALTY,
            "same-family penalty": SAME_FAMILY_PENALTY,
        }.get(effect, 0)
        
        # Get actual environmental context for this crop/month
        crop_obj = next((c for c in CROP_REGISTER if c.name == cycle.crop_name), None)
        if crop_obj:
            context = context_for(cycle.start_month, crop_obj)
        else:
            # Fallback: use demonstration context
            from app.domain.snapshot_context import context_from_demonstration
            context = context_from_demonstration(0, 0, cycle.start_month, cycle.duration_months)
        
        # Generate AI explanation for planting event
        ai_reason = await _generate_ai_explanation(
            crop_name=cycle.crop_name,
            context=context,
            month=cycle.start_month,
            suitability_index=cycle.suitability_index,
            limiting_factor=cycle.limiting_factor,
            previous_crop=previous.crop_name if previous else None,
            rotation_effect=effect,
            farm_id=farm_id,
        )
        
        for offset, month in enumerate(cycle.occupied_months):
            stage = _stage_for_month(offset, cycle.duration_months)
            by_month[month] = TimelineItem(
                month=month,
                month_name=calendar.month_name[month],
                crop_name=cycle.crop_name,
                stage=stage,
                action="plant" if stage == "planting" else "harvest" if stage == "harvest" else "continue",
                season_id=season_id,
                suitability_index=cycle.suitability_index if stage == "planting" else None,
                planning_score=max(0, min(100, cycle.suitability_index + adjustment)) if stage == "planting" else None,
                plant_month=cycle.start_month,
                harvest_month=cycle.harvest_month,
                duration_months=cycle.duration_months,
                previous_crop=previous.crop_name if previous else None,
                rotation_effect=effect,
                reason=ai_reason if stage == "planting" else None,
                limiting_factor=cycle.limiting_factor if stage == "planting" else None,
                continues_next_year=cycle.continues_next_year,
                data_mode=cycle.data_mode,
                snapshot_id=cycle.snapshot_id,
            )
        for month in cycle.recovery_months:
            by_month[month] = TimelineItem(
                month=month,
                month_name=calendar.month_name[month],
                crop_name=None,
                stage="recovery",
                action="recover",
                season_id=f"recovery-{season_id}",
                reason="Rest the soil before the next crop",
            )
        previous = cycle

    return [by_month[month] for month in sorted(by_month)]


def _cycles_to_timeline(
    cycles: tuple[CropCycleCandidate, ...],
    saved_entries: list[PlanEntry],
    year: int,
) -> list[TimelineItem]:
    """Convert optimized cycles plus fixed saved occupancy to the public timeline."""
    by_month: dict[int, TimelineItem] = {}
    for entry in saved_entries:
        months = sorted(_entry_occupied_months(entry, year))
        if not months:
            continue
        crop = next((item for item in CROP_REGISTER if item.name.casefold() == entry.crop_name.casefold()), None)
        duration = crop.duration_months if crop else len(months)
        for offset, month in enumerate(months):
            is_planting = entry.planting_date.year == year and entry.planting_date.month == month
            is_harvest = entry.harvest_date.year == year and entry.harvest_date.month == month
            stage = "planting" if is_planting else "harvest" if is_harvest else "growing"
            by_month.setdefault(month, TimelineItem(
                month=month,
                month_name=calendar.month_name[month],
                crop_name=entry.crop_name,
                stage=stage,
                action="plant" if is_planting else "harvest" if is_harvest else "continue",
                season_id=f"saved-{entry.id}",
                suitability_index=entry.suitability_index if is_planting or month == months[0] else None,
                planning_score=entry.suitability_index if is_planting or month == months[0] else None,
                plant_month=entry.planting_date.month if entry.planting_date.year == year else None,
                harvest_month=entry.harvest_date.month if entry.harvest_date.year == year else None,
                duration_months=duration,
                reason="Saved in your farm calendar" if is_planting or month == months[0] else None,
                data_mode=entry.data_mode,
                snapshot_id=str(entry.snapshot_id) if entry.snapshot_id else None,
                saved=True,
            ))

    previous: CropCycleCandidate | None = None
    counts: dict[str, int] = {}
    for cycle in sorted(cycles, key=lambda item: (item.start_month, item.crop_name)):
        counts[cycle.crop_name] = counts.get(cycle.crop_name, 0) + 1
        season_id = f"{cycle.crop_name.lower().replace(' ', '-')}-{counts[cycle.crop_name]}"
        reason, effect = _rotation_reason(cycle, previous)
        adjustment = {
            "preferred": PREFERRED_ROTATION_BONUS,
            "same-crop penalty": SAME_CROP_PENALTY,
            "avoid": AVOID_ROTATION_PENALTY,
            "same-family penalty": SAME_FAMILY_PENALTY,
        }.get(effect, 0)
        for offset, month in enumerate(cycle.occupied_months):
            stage = _stage_for_month(offset, cycle.duration_months)
            by_month[month] = TimelineItem(
                month=month,
                month_name=calendar.month_name[month],
                crop_name=cycle.crop_name,
                stage=stage,
                action="plant" if stage == "planting" else "harvest" if stage == "harvest" else "continue",
                season_id=season_id,
                suitability_index=cycle.suitability_index if stage == "planting" else None,
                planning_score=max(0, min(100, cycle.suitability_index + adjustment)) if stage == "planting" else None,
                plant_month=cycle.start_month,
                harvest_month=cycle.harvest_month,
                duration_months=cycle.duration_months,
                previous_crop=previous.crop_name if previous else None,
                rotation_effect=effect,
                reason=reason if stage == "planting" else None,
                limiting_factor=cycle.limiting_factor if stage == "planting" else None,
                continues_next_year=cycle.continues_next_year,
                data_mode=cycle.data_mode,
                snapshot_id=cycle.snapshot_id,
            )
        for month in cycle.recovery_months:
            by_month[month] = TimelineItem(
                month=month,
                month_name=calendar.month_name[month],
                crop_name=None,
                stage="recovery",
                action="recover",
                season_id=f"recovery-{season_id}",
                reason="Rest the soil before the next crop",
            )
        previous = cycle

    return [by_month[month] for month in sorted(by_month)]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _derive_harvest_date(planting_date: date, crop_name: str) -> date:
    """
    Derive harvest_date from planting_date + crop.duration_months.

    Finds the crop in CROP_REGISTER (case-insensitive) and adds
    duration_months calendar months to planting_date.  Cross-year
    boundaries are handled correctly by calendar arithmetic.

    Requirements: 3.1, 3.2
    """
    crop = next(
        (c for c in CROP_REGISTER if c.name.lower() == crop_name.lower()),
        None,
    )
    if crop is None:
        raise FarmValidationError(
            field="body.crop_name",
            detail_code="INVALID_VALUE",
            message=f"Unknown crop name: {crop_name!r}",
        )

    # Add duration_months to planting_date using calendar arithmetic
    month = planting_date.month + crop.duration_months
    year = planting_date.year + (month - 1) // 12
    month = ((month - 1) % 12) + 1
    # Use last day of month if the day exceeds month length
    max_day = calendar.monthrange(year, month)[1]
    day = min(planting_date.day, max_day)
    return date(year, month, day)


async def _load_latest_snapshot(
    session: AsyncSession,
    farm_id: UUID,
    geometry_revision: int,
) -> AnalysisSnapshot | None:
    """Return the latest completed snapshot for the farm, or None."""
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


async def _check_overlap(
    session: AsyncSession,
    farm_id: UUID,
    planting_date: date,
    harvest_date: date,
    exclude_entry_id: UUID | None = None,
) -> PlanEntry | None:
    """
    Return the first existing entry whose date range overlaps [planting_date, harvest_date].

    Two ranges [a, b] and [c, d] overlap when a < d AND c < b.

    Requirements: 2.2, 2.3, 3.3
    """
    stmt = (
        select(PlanEntry)
        .where(
            PlanEntry.farm_id == farm_id,
            PlanEntry.planting_date < harvest_date,
            PlanEntry.harvest_date > planting_date,
        )
    )
    if exclude_entry_id is not None:
        stmt = stmt.where(PlanEntry.id != exclude_entry_id)

    result = await session.execute(stmt)
    return result.scalars().first()


async def _fetch_farm(
    session: AsyncSession,
    principal: Principal,
    farm_id: UUID,
) -> Farm:
    """Load a farm with ownership check; raises NotFoundError for wrong owner."""
    repo = FarmRepository(session, principal.user_id)
    farm = await repo.get_by_id(farm_id)
    await session.execute(select(Farm.id).where(Farm.id == farm_id).with_for_update())
    return farm


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------


async def get_annual_plan(
    session: AsyncSession,
    principal: Principal,
    farm_id: UUID,
    year: int,
    rainfall_change_pct: float = 0,
    temperature_change_c: float = 0,
    irrigation_mm: float | None = None,
    today: date | None = None,
) -> AnnualPlanResponse:
    """
    Build the annual plan response.

    For each of the 12 calendar months:
    - Build a SnapshotContext (snapshot-backed when available, else demonstration).
    - Rank all crops and take top 3.

    Overlay saved PlanEntry rows for the given farm and year.
    Include pending Change_Proposals for the farm.

    Requirements: 1.1, 1.2, 1.3, 1.4
    """
    farm = await _fetch_farm(session, principal, farm_id)
    # Attempt to load latest snapshot (shared across all 12 months)
    snapshot = await _load_latest_snapshot(
        session, farm.id, farm.current_geometry_revision
    )

    def context_for(month: int, crop: CropRequirements) -> SnapshotContext:
        context = _context(
            farm,
            snapshot,
            date(year, month, 1),
            crop,
            "irrigated" if irrigation_mm is not None else "rain_fed",
            irrigation_mm,
        )
        rainfall = context.rainfall_total_mm
        return replace(
            context,
            temperature_mean_c=(
                context.temperature_mean_c + temperature_change_c
                if context.temperature_mean_c is not None else None
            ),
            rainfall_total_mm=(
                rainfall * (1 + rainfall_change_pct / 100)
                if rainfall is not None else None
            ),
        )

    # Load saved entries before optimization because they are fixed occupancy.
    entries_result = await session.execute(
        select(PlanEntry)
        .where(
            PlanEntry.farm_id == farm.id,
        )
        .order_by(PlanEntry.planting_date)
    )
    entries = list(entries_result.scalars().all())

    # Filter entries to the requested year (planting_date year or harvest_date year)
    year_entries = [
        e for e in entries
        if e.planting_date < date(year + 1, 1, 1) and e.harvest_date > date(year, 1, 1)
    ]

    blocked_months: set[int] = set()
    for entry in year_entries:
        blocked_months.update(_entry_occupied_months(entry, year))

    current_date = today or date.today()
    earliest_start_month = (
        13 if year < current_date.year
        else current_date.month if year == current_date.year
        else 1
    )
    candidates, perennial_opportunities = _build_cycle_candidates(
        CROP_REGISTER,
        context_for,
        earliest_start_month=earliest_start_month,
        blocked_months=blocked_months,
    )
    try:
        optimized = CropPlanOptimizer().optimize(candidates)
        if optimized.solver_status not in {"OPTIMAL", "FEASIBLE", "EMPTY"}:
            optimized = deterministic_fallback(candidates)
    except Exception:
        logger.exception("Annual crop optimizer failed; using deterministic fallback.")
        optimized = deterministic_fallback(candidates)

    timeline = await _cycles_to_timeline_with_ai(
        optimized.selected_cycles, year_entries, year, str(farm.id), context_for
    )
    timeline_by_month = {item.month: item for item in timeline}
    months = []
    for month in range(1, 13):
        item = timeline_by_month.get(month)
        recommendations = [] if item is None or item.crop_name is None else [{
            "crop_name": item.crop_name,
            "suitability_index": item.suitability_index,
            "label": item.stage.title(),
            "limiting_factor": item.limiting_factor,
            "reason": item.reason,
            "season_id": item.season_id,
            "saved": item.saved,
        }]
        months.append(MonthRecommendation(
            month=month,
            month_name=calendar.month_name[month],
            data_mode=item.data_mode if item else "unavailable",
            snapshot_id=item.snapshot_id if item else None,
            recommendations=recommendations,
        ))

    logger.info(
        "annual_plan_optimized farm_id=%s year=%s candidate_count=%s "
        "solver_status=%s selected_cycle_count=%s solve_ms=%s fallback_used=%s",
        farm.id,
        year,
        len(candidates),
        optimized.solver_status,
        len(optimized.selected_cycles),
        optimized.solve_ms,
        optimized.fallback_used,
    )

    # Load pending proposals for this farm
    proposals_result = await session.execute(
        select(ChangeProposal)
        .where(
            ChangeProposal.farm_id == farm.id,
            ChangeProposal.status == ProposalStatus.PENDING,
        )
        .order_by(ChangeProposal.issue_date)
    )
    proposals = list(proposals_result.scalars().all())

    return AnnualPlanResponse(
        farm_id=str(farm.id),
        year=year,
        months=months,
        timeline=timeline,
        perennial_opportunities=perennial_opportunities,
        entries=year_entries,
        proposals=proposals,
        solver_status=optimized.solver_status,
        fallback_used=optimized.fallback_used,
    )


async def create_entry(
    session: AsyncSession,
    principal: Principal,
    farm_id: UUID,
    data: PlanEntryCreate,
) -> PlanEntry:
    """
    Create a new PlanEntry with overlap validation and harvest date derivation.

    Steps:
    1. Ownership check on farm.
    2. Resolve crop from register; derive harvest_date.
    3. Validate no overlap with existing entries.
    4. Persist and return new entry.

    Requirements: 2.1, 2.2, 2.3, 3.1, 3.2
    """
    farm = await _fetch_farm(session, principal, farm_id)

    # Derive harvest date from planting_date + crop.duration_months (Req 3.1)
    harvest_date = _derive_harvest_date(data.planting_date, data.crop_name)

    await _validate_allocation(session, farm, data.planting_date, harvest_date, data.area_ha, data.field_name)
    # Load snapshot to capture data_mode and snapshot_id at save time
    snapshot = await _load_latest_snapshot(
        session, farm.id, farm.current_geometry_revision
    )

    # Build context for suitability score at save time
    from geoalchemy2.shape import to_shape as _to_shape
    centroid = _to_shape(farm.current_geometry.centroid)
    crop_obj = next(
        c for c in CROP_REGISTER if c.name.lower() == data.crop_name.lower()
    )
    context = _context(farm, snapshot, data.planting_date, crop_obj, data.cultivation_mode, data.irrigation_mm)

    from app.domain.crop_engine import score as _score
    result = _score(crop_obj, context)

    entry = PlanEntry(
        farm_id=farm.id,
        crop_name=crop_obj.name,
        field_name=data.field_name,
        planting_date=data.planting_date,
        harvest_date=harvest_date,
        cultivation_mode=data.cultivation_mode,
        irrigation_mm=data.irrigation_mm,
        area_ha=data.area_ha,
        snapshot_id=snapshot.id if snapshot is not None else None,
        data_mode=context.data_mode,
        suitability_index=result.suitability_index,
        engine_version=ENGINE_VERSION,
    )
    session.add(entry)
    await session.flush()
    await session.refresh(entry)
    await session.commit()
    return entry


async def update_entry(
    session: AsyncSession,
    principal: Principal,
    farm_id: UUID,
    entry_id: UUID,
    data: PlanEntryUpdate,
) -> PlanEntry:
    """
    Update an existing PlanEntry; re-validates overlap.

    Requirements: 2.5, 3.3
    """
    farm = await _fetch_farm(session, principal, farm_id)

    result = await session.execute(
        select(PlanEntry).where(
            PlanEntry.id == entry_id,
            PlanEntry.farm_id == farm.id,
        )
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise NotFoundError(f"Plan entry {entry_id} not found")

    if data.expected_revision is not None and data.expected_revision != entry.revision:
        raise FarmValidationError(field="expected_revision", detail_code="STALE_REVISION", message="This entry changed. Reload before saving.")
    # Apply updates
    new_planting = data.planting_date if data.planting_date is not None else entry.planting_date
    new_cultivation = data.cultivation_mode if data.cultivation_mode is not None else entry.cultivation_mode
    new_irrigation = data.irrigation_mm if data.irrigation_mm is not None else entry.irrigation_mm
    new_area = data.area_ha if data.area_ha is not None else entry.area_ha

    # Re-derive harvest date if planting date changed
    new_harvest = _derive_harvest_date(new_planting, entry.crop_name)

    field_name = data.field_name if data.field_name is not None else entry.field_name
    if new_cultivation == "irrigated" and new_irrigation is None:
        raise FarmValidationError(field="irrigation_mm", detail_code="REQUIRED", message="Monthly irrigation is required.")
    if new_cultivation == "rain_fed":
        new_irrigation = None
    await _validate_allocation(session, farm, new_planting, new_harvest, new_area, field_name, entry.id)
    snapshot = await _load_latest_snapshot(session, farm.id, farm.current_geometry_revision)
    crop = next(c for c in CROP_REGISTER if c.name == entry.crop_name)
    context = _context(farm, snapshot, new_planting, crop, new_cultivation, new_irrigation)
    entry.suitability_index = score(crop, context).suitability_index
    entry.snapshot_id = snapshot.id if snapshot else None
    entry.data_mode = context.data_mode
    entry.engine_version = ENGINE_VERSION
    entry.revision += 1
    entry.field_name = field_name
    # Prior proposals no longer apply to the edited entry.
    for proposal in (await session.execute(select(ChangeProposal).where(ChangeProposal.entry_id == entry.id, ChangeProposal.status == ProposalStatus.PENDING))).scalars():
        proposal.status = ProposalStatus.DISMISSED

    entry.planting_date = new_planting
    entry.harvest_date = new_harvest
    entry.cultivation_mode = new_cultivation
    entry.irrigation_mm = new_irrigation
    entry.area_ha = new_area

    await session.flush()
    await session.refresh(entry)
    await session.commit()
    return entry


async def delete_entry(
    session: AsyncSession,
    principal: Principal,
    farm_id: UUID,
    entry_id: UUID,
) -> None:
    """
    Delete a PlanEntry; ownership-scoped.

    Requirements: 2.6
    """
    farm = await _fetch_farm(session, principal, farm_id)

    result = await session.execute(
        select(PlanEntry).where(
            PlanEntry.id == entry_id,
            PlanEntry.farm_id == farm.id,
        )
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise NotFoundError(f"Plan entry {entry_id} not found")

    await session.delete(entry)
    await session.flush()
    await session.commit()


async def dismiss_proposal(
    session: AsyncSession,
    principal: Principal,
    farm_id: UUID,
    proposal_id: UUID,
) -> None:
    """
    Dismiss a Change_Proposal: mark it as dismissed, leaving the linked
    PlanEntry unchanged.

    Requirements: 4.4
    """
    farm = await _fetch_farm(session, principal, farm_id)

    result = await session.execute(
        select(ChangeProposal).where(
            ChangeProposal.id == proposal_id,
            ChangeProposal.farm_id == farm.id,
            ChangeProposal.status == ProposalStatus.PENDING,
        )
    )
    proposal = result.scalar_one_or_none()
    if proposal is None:
        raise NotFoundError(f"Change proposal {proposal_id} not found or not pending")

    proposal.status = ProposalStatus.DISMISSED

    await session.flush()
    await session.commit()


async def accept_proposal(
    session: AsyncSession,
    principal: Principal,
    farm_id: UUID,
    proposal_id: UUID,
) -> PlanEntry:
    """
    Accept a Change_Proposal: update the linked PlanEntry's suitability index
    and mark the proposal as accepted.

    Requirements: 4.3, 4.4
    """
    farm = await _fetch_farm(session, principal, farm_id)

    # Load proposal (ownership-scoped via farm_id)
    result = await session.execute(
        select(ChangeProposal).where(
            ChangeProposal.id == proposal_id,
            ChangeProposal.farm_id == farm.id,
            ChangeProposal.status == ProposalStatus.PENDING,
        )
    )
    proposal = result.scalar_one_or_none()
    if proposal is None:
        raise NotFoundError(f"Change proposal {proposal_id} not found or not pending")

    # Load the linked entry
    entry_result = await session.execute(
        select(PlanEntry).where(PlanEntry.id == proposal.entry_id, PlanEntry.farm_id == farm.id)
    )
    entry = entry_result.scalar_one_or_none()
    if entry is None:
        raise NotFoundError(f"Plan entry {proposal.entry_id} not found")

    expected = (proposal.changed_inputs or {}).get("entry_revision")
    if expected is not None and expected != entry.revision:
        raise FarmValidationError(field="proposal", detail_code="STALE_REVISION", message="This proposal belongs to an older plan entry revision.")
    entry.revision += 1
    entry.engine_version = ENGINE_VERSION
    # Apply the proposal's new recommendation
    entry.suitability_index = proposal.new_suitability_index
    entry.snapshot_id = proposal.new_snapshot_id

    # Mark proposal as accepted (Req 4.3)
    proposal.status = ProposalStatus.ACCEPTED

    await session.flush()
    await session.refresh(entry)
    await session.commit()
    return entry


def _context(farm, snapshot, planting_date, crop, mode="rain_fed", irrigation=None):
    if snapshot is not None:
        context = context_from_snapshot(snapshot, planting_date.month, crop.duration_months, planting_date)
    elif Settings().data_mode.value == "demonstration":
        centroid = to_shape(farm.current_geometry.centroid)
        context = context_from_demonstration(centroid.y, centroid.x, planting_date.month, crop.duration_months)
    else:
        from types import SimpleNamespace
        context = context_from_snapshot(SimpleNamespace(id=None, data_mode=Settings().data_mode.value,
            weather=None, climate_baseline=None, satellite=None, soil=None, terrain=None, conduit=None),
            planting_date.month, crop.duration_months, planting_date)
        from dataclasses import replace
        context = replace(context, snapshot_id=None)
    return with_irrigation(context, mode, irrigation, crop.duration_months)


async def _validate_allocation(session, farm, start, end, area, field_name, exclude_id=None):
    """Sweep occupancy events; allow simultaneous crops only within farm capacity."""
    import math
    capacity = farm.current_geometry.hectares
    if not math.isfinite(area) or area <= 0 or area > capacity:
        raise FarmValidationError(field="area_ha", detail_code="AREA_EXCEEDED", message=f"Allocate between 0 and {capacity:.4f} hectares.")
    query = select(PlanEntry).where(PlanEntry.farm_id == farm.id, PlanEntry.planting_date < end, PlanEntry.harvest_date > start)
    if exclude_id:
        query = query.where(PlanEntry.id != exclude_id)
    rows = (await session.execute(query)).scalars().all()
    events = [(start, area), (end, -area)]
    for entry in rows:
        if field_name and entry.field_name and field_name.strip().casefold() == entry.field_name.strip().casefold():
            raise FarmValidationError(field="field_name", detail_code="OVERLAP_CONFLICT", message="This field is already occupied during that growing period.")
        events.extend([(max(start, entry.planting_date), entry.area_ha), (min(end, entry.harvest_date), -entry.area_ha)])
    occupied = 0.0
    for day, change in sorted(events, key=lambda e: (e[0], e[1])):
        occupied += change
        if occupied > capacity + 1e-8:
            raise FarmValidationError(field="area_ha", detail_code="OVERLAP_CONFLICT", message=f"Allocations exceed farm area on {day}. Reduce area or change planting dates.")


async def generate_proposals(session, snapshot):
    """Persist material score/evidence changes without modifying the farmer's plan."""
    from datetime import datetime, timezone
    from dataclasses import asdict
    farm = (await session.execute(select(Farm).where(Farm.id == snapshot.farm_id).with_for_update())).scalar_one()
    if farm.current_geometry_revision != snapshot.geometry_revision:
        return
    entries = (await session.execute(select(PlanEntry).where(PlanEntry.farm_id == farm.id, PlanEntry.harvest_date >= datetime.now(timezone.utc).date()))).scalars().all()
    for entry in entries:
        crop = next((c for c in CROP_REGISTER if c.name == entry.crop_name), None)
        if crop is None:
            continue
        context = _context(farm, snapshot, entry.planting_date, crop, entry.cultivation_mode, entry.irrigation_mm)
        new_score = score(crop, context).suitability_index
        old_score = entry.suitability_index
        if new_score == old_score or (new_score is not None and old_score is not None and abs(new_score - old_score) < 5):
            continue
        pending = (await session.execute(select(ChangeProposal).where(ChangeProposal.entry_id == entry.id, ChangeProposal.status == ProposalStatus.PENDING))).scalars().all()
        if any(p.new_snapshot_id == snapshot.id for p in pending):
            continue
        for proposal in pending:
            proposal.status = ProposalStatus.DISMISSED
        session.add(ChangeProposal(farm_id=farm.id, entry_id=entry.id,
            old_suitability_index=old_score, new_suitability_index=new_score,
            new_snapshot_id=snapshot.id, status=ProposalStatus.PENDING,
            changed_inputs={"entry_revision": entry.revision, "snapshot": {"old": str(entry.snapshot_id) if entry.snapshot_id else None, "new": str(snapshot.id)},
                            "temperature_mean_c": context.temperature_mean_c, "rainfall_total_mm": context.rainfall_total_mm}))
