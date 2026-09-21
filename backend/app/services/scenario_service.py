"""
Scenario delta engine service.

Applies hypothetical climate changes to a baseline SnapshotContext,
recomputes VPD and dependent features, runs crop and risk engines,
and persists results as immutable SavedScenario records.

Public API:
  apply_delta(context, delta) -> SnapshotContext
  compute_scenario(session, principal, farm_id, delta, name) -> ScenarioResponse
  list_scenarios(session, principal, farm_id) -> list[ScenarioSummary]

Requirements: 2.2, 2.5, 2.6, 2.7, 3.1, 3.2, 3.3, 3.4
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.security import Principal
from app.domain.crop_engine import ENGINE_VERSION as CROP_ENGINE_VERSION
from app.domain.crop_engine import SimulationResult, rank_all, score
from app.domain.crop_register import CROP_REGISTER
from app.domain.risk_engine import ENGINE_VERSION as RISK_ENGINE_VERSION
from app.domain.risk_engine import RiskResponse, assess_all
from app.domain.snapshot_context import SnapshotContext, context_from_snapshot
from app.models.scenario import SavedScenario
from app.models.snapshot import AnalysisSnapshot
from app.repositories.farm import FarmRepository

logger = logging.getLogger(__name__)

# Combined engine version string for scenario records
ENGINE_VERSION = f"{CROP_ENGINE_VERSION}+{RISK_ENGINE_VERSION}"


# ---------------------------------------------------------------------------
# ScenarioDelta
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScenarioDelta:
    """
    User-supplied bounded deltas for scenario exploration.

    Requirements: 2.1
    """
    rainfall_change_pct: float = 0.0     # −50 to +50
    temperature_change_c: float = 0.0    # −5 to +5
    irrigation_mm_override: float | None = None


# ---------------------------------------------------------------------------
# VPD recomputation
# ---------------------------------------------------------------------------

def _compute_vpd(temperature_c: float, relative_humidity_pct: float = 60.0) -> float:
    """
    Compute vapour pressure deficit (kPa) from temperature and relative humidity.

    Uses the Tetens saturation vapour pressure formula:
        sat_vp = 0.6108 * exp(17.27 * T / (T + 237.3))
        VPD = sat_vp * (1 - RH / 100)

    A neutral relative humidity of 60% is assumed when not explicitly provided,
    consistent with the demonstration profile.

    Requirements: 3.2
    """
    sat_vp = 0.6108 * math.exp(17.27 * temperature_c / (temperature_c + 237.3))
    return sat_vp * (1.0 - relative_humidity_pct / 100.0)


# ---------------------------------------------------------------------------
# apply_delta
# ---------------------------------------------------------------------------

def apply_delta(context: SnapshotContext, delta: ScenarioDelta) -> SnapshotContext:
    """
    Return a new SnapshotContext with the scenario delta applied.

    Modifications:
    1. Adjust rainfall_total_mm by delta.rainfall_change_pct (percentage change).
    2. Adjust temperature_mean_c by delta.temperature_change_c.
    3. Recompute VPD from the modified temperature using the Tetens formula.
    4. Override irrigation_mm (tracked in VPD proxy only — no direct field).

    Zero delta → identical inputs and identical engine outputs (Property 1).
    All other fields are preserved unchanged from the original context.

    Requirements: 2.2, 3.1, 3.2, 3.3
    """
    # --- Rainfall adjustment ---
    new_rainfall: float | None = context.rainfall_total_mm
    if new_rainfall is not None:
        multiplier = 1.0 + delta.rainfall_change_pct / 100.0
        new_rainfall = max(0.0, new_rainfall * multiplier)
    elif delta.irrigation_mm_override is not None:
        # When no baseline rainfall but irrigation is set, use override directly
        new_rainfall = delta.irrigation_mm_override

    # Handle irrigation override on top of adjusted rainfall
    if delta.irrigation_mm_override is not None and context.rainfall_total_mm is not None:
        new_rainfall = (new_rainfall or 0.0) + delta.irrigation_mm_override

    # --- Temperature adjustment ---
    new_temp: float | None = context.temperature_mean_c
    if new_temp is not None:
        new_temp = new_temp + delta.temperature_change_c

    # --- VPD recomputation from modified temperature (Requirement 3.2) ---
    # Only recompute VPD when temperature is available; otherwise keep original.
    new_vpd: float | None = context.vpd_kpa
    if new_temp is not None and delta.temperature_change_c != 0.0:
        new_vpd = _compute_vpd(new_temp)

    # When delta is zero, all values remain identical to the baseline context.
    # We still replace vpd_kpa with the baseline's VPD value (no change).

    # --- Build new real/demo field sets ---
    # Fields we modified should remain in their original category
    new_real_fields = set(context.real_input_fields)
    new_demo_fields = set(context.demonstration_input_fields)

    # If we now have values for fields that were previously missing, add them
    if new_rainfall is not None and "rainfall_total_mm" not in new_real_fields:
        if delta.irrigation_mm_override is not None:
            new_real_fields.add("rainfall_total_mm")
            new_demo_fields.discard("rainfall_total_mm")

    return SnapshotContext(
        source=context.source,
        snapshot_id=context.snapshot_id,
        data_mode=context.data_mode,
        temperature_mean_c=new_temp,
        temperature_source=context.temperature_source,
        rainfall_total_mm=new_rainfall,
        rainfall_source=context.rainfall_source,
        soil_ph=context.soil_ph,
        soil_clay_pct=context.soil_clay_pct,
        soil_sand_pct=context.soil_sand_pct,
        soil_source=context.soil_source,
        soil_is_modelled=context.soil_is_modelled,
        ndvi_mean=context.ndvi_mean,
        ndvi_source=context.ndvi_source,
        vpd_kpa=new_vpd,
        real_input_fields=frozenset(new_real_fields),
        demonstration_input_fields=frozenset(new_demo_fields),
        rain_7d_mm=context.rain_7d_mm,
        wind_max_ms=context.wind_max_ms,
        slope_pct=context.slope_pct,
        climate_baseline_rainfall_mm=context.climate_baseline_rainfall_mm,
    )


# ---------------------------------------------------------------------------
# Response schemas (lightweight dataclasses — API schemas live in api/v1)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CropScenarioResult:
    """Baseline vs scenario suitability for one crop."""
    crop_name: str
    baseline_index: int
    scenario_index: int
    baseline_label: str
    scenario_label: str


@dataclass(frozen=True)
class HazardScenarioResult:
    """Baseline vs scenario hazard level for one hazard."""
    hazard: str
    baseline_level: str
    scenario_level: str
    baseline_index: int
    scenario_index: int


@dataclass(frozen=True)
class ScenarioResponse:
    """Full scenario computation result."""
    id: str
    farm_id: str
    name: str
    baseline_snapshot_id: str | None
    delta: dict[str, Any]
    crops: list[CropScenarioResult]
    hazards: list[HazardScenarioResult]
    engine_version: str
    created_at: str


@dataclass(frozen=True)
class ScenarioSummary:
    """Summary of a saved scenario for list responses."""
    id: str
    farm_id: str
    name: str
    baseline_snapshot_id: str | None
    delta: dict[str, Any]
    engine_version: str
    created_at: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _results_to_dict(
    baseline_crops: list[SimulationResult],
    scenario_crops: list[SimulationResult],
    baseline_risk: RiskResponse,
    scenario_risk: RiskResponse,
) -> dict[str, Any]:
    """Serialize crop and hazard results to a JSON-compatible dict for persistence."""
    crop_results = []
    # Build a lookup from the scenario results by crop name
    scenario_by_name = {r.crop_name: r for r in scenario_crops}
    for b in baseline_crops:
        s = scenario_by_name.get(b.crop_name)
        crop_results.append({
            "crop_name": b.crop_name,
            "baseline_index": b.suitability_index,
            "scenario_index": s.suitability_index if s else b.suitability_index,
            "baseline_label": b.label,
            "scenario_label": s.label if s else b.label,
        })

    hazard_results = []
    scenario_hazard_by_name = {a.hazard: a for a in scenario_risk.assessments}
    for b in baseline_risk.assessments:
        s = scenario_hazard_by_name.get(b.hazard)
        hazard_results.append({
            "hazard": b.hazard,
            "baseline_level": b.level,
            "scenario_level": s.level if s else b.level,
            "baseline_index": b.index,
            "scenario_index": s.index if s else b.index,
        })

    return {
        "crops": crop_results,
        "hazards": hazard_results,
    }


def _load_scenario_results(saved: SavedScenario) -> tuple[list[CropScenarioResult], list[HazardScenarioResult]]:
    """Deserialise persisted results back into response objects."""
    results = saved.results or {}
    crops = [
        CropScenarioResult(
            crop_name=c["crop_name"],
            baseline_index=c["baseline_index"],
            scenario_index=c["scenario_index"],
            baseline_label=c["baseline_label"],
            scenario_label=c["scenario_label"],
        )
        for c in results.get("crops", [])
    ]
    hazards = [
        HazardScenarioResult(
            hazard=h["hazard"],
            baseline_level=h["baseline_level"],
            scenario_level=h["scenario_level"],
            baseline_index=h["baseline_index"],
            scenario_index=h["scenario_index"],
        )
        for h in results.get("hazards", [])
    ]
    return crops, hazards


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

async def compute_scenario(
    session: AsyncSession,
    principal: Principal,
    farm_id: UUID,
    delta: ScenarioDelta,
    name: str,
) -> ScenarioResponse:
    """
    Load the latest snapshot for farm_id, apply the delta, run both engines,
    and save an immutable SavedScenario record.

    Steps:
    1. Ownership check via FarmRepository (raises NotFoundError → 404).
    2. Load the latest AnalysisSnapshot for the current geometry revision.
    3. Build baseline SnapshotContext.
    4. Apply delta to get scenario context.
    5. Run crop engine and risk engine on both contexts.
    6. Persist SavedScenario (immutable — requirement 2.7).
    7. Return ScenarioResponse.

    Requirements: 2.5, 2.6, 2.7, 3.1, 3.4
    """
    # 1. Ownership check
    repo = FarmRepository(session, principal.user_id)
    farm = await repo.get_by_id(farm_id)

    # 2. Load latest snapshot
    result = await session.execute(
        select(AnalysisSnapshot)
        .where(
            AnalysisSnapshot.farm_id == farm.id,
            AnalysisSnapshot.geometry_revision == farm.current_geometry_revision,
        )
        .order_by(desc(AnalysisSnapshot.created_at))
        .limit(1)
    )
    snapshot: AnalysisSnapshot | None = result.scalar_one_or_none()

    if snapshot is None:
        raise NotFoundError(
            f"No analysis snapshot available for farm {farm_id}. "
            "Run an analysis job first."
        )

    # 3. Build baseline context (use planting_month=1, crop_duration=4 as defaults)
    baseline_context = context_from_snapshot(snapshot, planting_month=1, crop_duration=4)

    # 4. Apply delta
    scenario_context = apply_delta(baseline_context, delta)

    # 5. Run engines on both contexts
    baseline_crops = rank_all(baseline_context)
    scenario_crops = rank_all(scenario_context)
    baseline_risk = assess_all(baseline_context, str(farm.id))
    scenario_risk = assess_all(scenario_context, str(farm.id))

    # 6. Persist as immutable SavedScenario
    delta_dict = {
        "rainfall_change_pct": delta.rainfall_change_pct,
        "temperature_change_c": delta.temperature_change_c,
        "irrigation_mm_override": delta.irrigation_mm_override,
    }
    results_dict = _results_to_dict(
        baseline_crops, scenario_crops, baseline_risk, scenario_risk
    )

    saved = SavedScenario(
        id=uuid.uuid4(),
        farm_id=farm.id,
        name=name,
        baseline_snapshot_id=snapshot.id,
        delta=delta_dict,
        results=results_dict,
        engine_version=ENGINE_VERSION,
    )
    session.add(saved)
    await session.flush()
    await session.commit()

    logger.debug(
        "Saved scenario %s for farm %s (snapshot=%s)", saved.id, farm_id, snapshot.id
    )

    # 7. Build response
    crops_out, hazards_out = _load_scenario_results(saved)
    return ScenarioResponse(
        id=str(saved.id),
        farm_id=str(farm.id),
        name=saved.name,
        baseline_snapshot_id=str(saved.baseline_snapshot_id),
        delta=delta_dict,
        crops=crops_out,
        hazards=hazards_out,
        engine_version=saved.engine_version,
        created_at=saved.created_at.isoformat(),
    )


async def list_scenarios(
    session: AsyncSession,
    principal: Principal,
    farm_id: UUID,
) -> list[ScenarioSummary]:
    """
    Return all saved scenarios for farm_id (ownership check included).

    Requirements: 2.6, 2.7
    """
    # Ownership check
    repo = FarmRepository(session, principal.user_id)
    await repo.get_by_id(farm_id)

    result = await session.execute(
        select(SavedScenario)
        .where(SavedScenario.farm_id == farm_id)
        .order_by(desc(SavedScenario.created_at))
    )
    rows = result.scalars().all()

    return [
        ScenarioSummary(
            id=str(s.id),
            farm_id=str(s.farm_id),
            name=s.name,
            baseline_snapshot_id=str(s.baseline_snapshot_id) if s.baseline_snapshot_id else None,
            delta=s.delta or {},
            engine_version=s.engine_version,
            created_at=s.created_at.isoformat(),
        )
        for s in rows
    ]
