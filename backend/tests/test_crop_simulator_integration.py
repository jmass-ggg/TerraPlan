"""
Backend integration tests for Phase 6 — Crop Simulator checkpoint.

Covers:
- All 12 crops score and rank under demonstration context.
- Simulate with real snapshot: snapshot_id present, real soil/NDVI values used.
- Hard exclusion: extreme temperature context → suitability_index = 0.
- Missing NDVI: neutral 65 used, "environmental_condition" in demonstration_input_fields.
- Cross-user: POST simulate-crop on another user's farm returns 404 (ownership isolation).

Requirements: 2.1, 2.2, 3.3, 3.4, 4.1, 6.4
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.api.v1.crop_schemas import CropRankingResponse, SimulationResponse
from app.api.v1.farm_schemas import FarmCreate
from app.core.config import Settings, DataMode
from app.core.security import Principal
from app.data.providers.base import (
    EVIDENCE_ACCEPTED,
    EVIDENCE_UNAVAILABLE,
    ProviderResult,
)
from app.domain.crop_engine import _NEUTRAL_NDVI_SCORE, rank_all, score
from app.domain.crop_register import CROP_REGISTER
from app.domain.snapshot_context import context_from_demonstration, context_from_snapshot
from app.models.snapshot import AnalysisJob, AnalysisSnapshot, JobStatus
from app.models.user import User
from app.repositories.base import NotFoundError
from app.services.crop_service import simulate
from app.services.farm_service import FarmService
from app.services.snapshot_service import run_analysis_job


# ---------------------------------------------------------------------------
# Geometry and settings helpers
# ---------------------------------------------------------------------------

FARM_BOUNDARY = {
    "type": "Polygon",
    "coordinates": [
        [[36.80, -1.30], [36.805, -1.30], [36.805, -1.295], [36.80, -1.30]]
    ],
}


def _settings_historical() -> Settings:
    env_path = Path(__file__).parent.parent / ".env"
    return Settings(_env_file=str(env_path), data_mode="historical_replay")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _create_user(session_factory, subject: str) -> User:
    async with session_factory() as session:
        user = User(
            id=uuid.uuid4(),
            issuer="crop-simulator-integration-tests",
            subject=subject,
            email=f"{subject}@example.test",
            display_name=subject,
        )
        session.add(user)
        await session.commit()
        return user


async def _create_farm(session_factory, user: User):
    async with session_factory() as session:
        service = FarmService(session, user.id)
        result = await service.create_farm_request(
            FarmCreate(name="Crop Test Farm", geometry=FARM_BOUNDARY)
        )
        return result


def _make_principal(user_id: uuid.UUID) -> Principal:
    return Principal(
        user_id=user_id,
        issuer="crop-simulator-integration-tests",
        subject=str(user_id),
        permissions=frozenset(),
    )


# ---------------------------------------------------------------------------
# Mock provider results
# ---------------------------------------------------------------------------

def _accepted_weather_result() -> ProviderResult:
    return ProviderResult(
        payload={
            "fields": {
                "temperature_2m": {
                    "value": 24.5,
                    "unit": "celsius",
                    "source": "open-meteo",
                    "acquired_at": "2026-09-07T06:00:00Z",
                    "retrieved_at": "2026-09-07T09:00:00Z",
                    "data_mode": "historical_replay",
                    "quality": "accepted",
                    "resolution_m": 11000,
                }
            }
        },
        evidence_status=EVIDENCE_ACCEPTED,
        error_message=None,
    )


def _accepted_soil_result() -> ProviderResult:
    """Build an accepted soil payload matching the structure from soil.py."""
    retrieved_at = "2026-09-07T09:00:00Z"
    return ProviderResult(
        payload={
            "depths": {
                "0-5cm": {
                    "phh2o": {
                        "mean": {
                            "value": 6.2,
                            "unit": "dimensionless",
                            "source": "soilgrids-v2",
                            "acquired_at": retrieved_at,
                            "retrieved_at": retrieved_at,
                            "data_mode": "historical_replay",
                            "quality": "accepted",
                            "resolution_m": 250.0,
                            "modelled_estimate": True,
                        },
                        "uncertainty_5th_percentile": None,
                        "uncertainty_95th_percentile": None,
                        "property_label": "pH (water)",
                        "depth": "0-5cm",
                    },
                    "clay": {
                        "mean": {
                            "value": 28.0,
                            "unit": "percent",
                            "source": "soilgrids-v2",
                            "acquired_at": retrieved_at,
                            "retrieved_at": retrieved_at,
                            "data_mode": "historical_replay",
                            "quality": "accepted",
                            "resolution_m": 250.0,
                            "modelled_estimate": True,
                        },
                        "uncertainty_5th_percentile": None,
                        "uncertainty_95th_percentile": None,
                        "property_label": "clay fraction",
                        "depth": "0-5cm",
                    },
                    "sand": {
                        "mean": {
                            "value": 40.0,
                            "unit": "percent",
                            "source": "soilgrids-v2",
                            "acquired_at": retrieved_at,
                            "retrieved_at": retrieved_at,
                            "data_mode": "historical_replay",
                            "quality": "accepted",
                            "resolution_m": 250.0,
                            "modelled_estimate": True,
                        },
                        "uncertainty_5th_percentile": None,
                        "uncertainty_95th_percentile": None,
                        "property_label": "sand fraction",
                        "depth": "0-5cm",
                    },
                }
            },
            "source_resolution_m": 250,
            "resolution_note": "SoilGrids nominal resolution: 250 m",
            "smaller_than_grid_cell": False,
            "grid_cell_area_ha": 0.625,
            "farm_area_ha": 1.5,
            "modelled_estimate": True,
            "retrieved_at": retrieved_at,
        },
        evidence_status=EVIDENCE_ACCEPTED,
        error_message=None,
    )


def _accepted_satellite_result() -> ProviderResult:
    """Build an accepted satellite payload with NDVI value."""
    retrieved_at = "2026-09-07T09:00:00Z"
    return ProviderResult(
        payload={
            "ndvi": {
                "value": 0.55,
                "unit": "dimensionless",
                "source": "sentinel-2-l2a",
                "acquired_at": "2026-09-01T08:30:00Z",
                "retrieved_at": retrieved_at,
                "data_mode": "historical_replay",
                "quality": "accepted",
                "resolution_m": 10,
            },
            "ndmi": {
                "value": 0.32,
                "unit": "dimensionless",
                "source": "sentinel-2-l2a",
                "acquired_at": "2026-09-01T08:30:00Z",
                "retrieved_at": retrieved_at,
                "data_mode": "historical_replay",
                "quality": "accepted",
                "resolution_m": 20,
            },
            "valid_pixel_pct": 95.0,
            "valid_pixel_count": 1200,
            "total_pixel_count": 1264,
            "scene_id": "S2A_MSIL2A_20260901T075611",
            "acquisition_date": "2026-09-01T08:30:00Z",
            "cloud_cover_pct": 2.1,
            "last_scene_age_days": None,
            "unavailability_reason": None,
            "ndvi_std": 0.08,
            "ndmi_std": 0.05,
            "ndmi_note": "Spectral moisture proxy.",
        },
        evidence_status=EVIDENCE_ACCEPTED,
        error_message=None,
    )


def _accepted_climate_result() -> ProviderResult:
    retrieved_at = "2026-09-07T09:00:00Z"
    return ProviderResult(
        payload={
            "all_monthly_means": {
                "temperature_2m_mean": {
                    "3": 23.1, "4": 24.0, "5": 24.5, "6": 22.8,
                    "7": 21.5, "8": 22.0, "9": 23.5, "10": 24.2,
                },
                "precipitation_sum": {
                    "3": 80.0, "4": 95.0, "5": 60.0, "6": 30.0,
                    "7": 15.0, "8": 18.0, "9": 55.0, "10": 88.0,
                },
            },
            "temperature_2m_mean": {
                "baseline_monthly_mean": {
                    "value": 23.0,
                    "unit": "celsius",
                    "source": "open-meteo-era5",
                    "acquired_at": retrieved_at,
                    "retrieved_at": retrieved_at,
                    "data_mode": "historical_replay",
                    "quality": "accepted",
                    "resolution_m": 11000,
                }
            },
            "precipitation_sum": {
                "baseline_monthly_mean": {
                    "value": 60.0,
                    "unit": "mm",
                    "source": "open-meteo-era5",
                    "acquired_at": retrieved_at,
                    "retrieved_at": retrieved_at,
                    "data_mode": "historical_replay",
                    "quality": "accepted",
                    "resolution_m": 11000,
                }
            },
            "baseline_period": "1991-2020",
        },
        evidence_status=EVIDENCE_ACCEPTED,
        error_message=None,
    )


def _accepted_terrain_result() -> ProviderResult:
    return ProviderResult(
        payload={"mean_elevation_m": {"value": 1650.0}, "flood_probability": None},
        evidence_status=EVIDENCE_ACCEPTED,
        error_message=None,
    )


def _unavailable_result(source: str) -> ProviderResult:
    return ProviderResult(
        payload=None,
        evidence_status=EVIDENCE_UNAVAILABLE,
        error_message=f"{source} unavailable (mocked)",
    )


# ---------------------------------------------------------------------------
# Helper: run a full analysis job with mocked providers
# ---------------------------------------------------------------------------

async def _run_snapshot_job(session_factory, job_id, settings, *, include_satellite=True):
    satellite_mock = (
        AsyncMock(return_value=_accepted_satellite_result())
        if include_satellite
        else AsyncMock(return_value=_unavailable_result("satellite"))
    )
    with (
        patch("app.data.providers.weather.fetch", new=AsyncMock(return_value=_accepted_weather_result())),
        patch("app.data.providers.climate.fetch", new=AsyncMock(return_value=_accepted_climate_result())),
        patch("app.data.providers.satellite.fetch", new=satellite_mock),
        patch("app.data.providers.soil.fetch", new=AsyncMock(return_value=_accepted_soil_result())),
        patch("app.data.providers.terrain.fetch", new=AsyncMock(return_value=_accepted_terrain_result())),
    ):
        return await run_analysis_job(job_id, settings, session_factory)


# ---------------------------------------------------------------------------
# Test 1: All 12 crops score and rank under demonstration context
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_12_crops_rank_under_demonstration_context(session_factory):
    """All 12 crops in the register score and rank under a demonstration context.

    rank_all() must return exactly 12 results in descending suitability_index order.

    Requirements: 4.1, 2.2
    """
    user = await _create_user(session_factory, "demo-rank-all")
    farm_result = await _create_farm(session_factory, user)
    farm = farm_result.farm

    # Build demonstration context for the farm centroid (Kenya region)
    context = context_from_demonstration(
        latitude=-1.2975,
        longitude=36.8025,
        planting_month=4,
    )
    assert context.source == "demonstration"
    assert context.snapshot_id is None
    assert context.data_mode == "demonstration"

    # Score all crops
    ranked = rank_all(context)

    # Must return exactly 12 crops
    assert len(ranked) == 12, f"Expected 12 crops, got {len(ranked)}"

    # All crop names in the register must appear in the ranking
    register_names = {c.name for c in CROP_REGISTER}
    ranked_names = {r.crop_name for r in ranked}
    assert register_names == ranked_names, (
        f"Ranked crop names do not match register. "
        f"Missing: {register_names - ranked_names}, "
        f"Extra: {ranked_names - register_names}"
    )

    # Scores must be descending (non-increasing)
    for i in range(len(ranked) - 1):
        assert ranked[i].suitability_index >= ranked[i + 1].suitability_index, (
            f"Ranking not sorted at position {i}: "
            f"{ranked[i].crop_name}={ranked[i].suitability_index} < "
            f"{ranked[i + 1].crop_name}={ranked[i + 1].suitability_index}"
        )

    # Every score must be in [0, 100]
    for r in ranked:
        assert 0 <= r.suitability_index <= 100, (
            f"Suitability index out of range for {r.crop_name}: {r.suitability_index}"
        )
        # Label must be one of the three valid values
        assert r.label in ("Good match", "Possible match", "Higher caution"), (
            f"Invalid label for {r.crop_name}: {r.label!r}"
        )

    # data_mode must be demonstration across all results
    for r in ranked:
        assert r.data_mode == "demonstration"
        assert r.snapshot_id is None


@pytest.mark.asyncio
async def test_crop_service_rank_without_snapshot_uses_demonstration(session_factory):
    """simulate() with no snapshot falls back to demonstration and returns 12 crops.

    Requirements: 2.2, 4.1
    """
    user = await _create_user(session_factory, "service-demo-rank")
    farm_result = await _create_farm(session_factory, user)
    farm = farm_result.farm
    principal = _make_principal(user.id)

    # Do NOT run the analysis job — no snapshot exists
    async with session_factory() as session:
        response = await simulate(
            session=session,
            principal=principal,
            farm_id=farm.id,
            crop_name=None,
            planting_date=date(2027, 4, 1),
            cultivation_mode="rain_fed",
            irrigation_mm=None,
        )

    assert isinstance(response, CropRankingResponse)
    assert response.data_mode == "demonstration"
    assert response.snapshot_id is None
    assert len(response.ranked) == 12

    # Sorted descending
    for i in range(len(response.ranked) - 1):
        assert response.ranked[i].suitability_index >= response.ranked[i + 1].suitability_index


# ---------------------------------------------------------------------------
# Test 2: Simulate with real snapshot: snapshot_id present, real values used
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_simulate_with_real_snapshot_uses_snapshot_data(session_factory):
    """When a real snapshot exists, simulate() returns snapshot_id and real data_mode.

    Soil pH and NDVI values from the snapshot must influence the score
    (completeness should show at least some 'real' inputs).

    Requirements: 2.1, 6.4
    """
    user = await _create_user(session_factory, "real-snapshot-sim")
    farm_result = await _create_farm(session_factory, user)
    farm = farm_result.farm
    job_id = farm_result.analysis_job_id
    principal = _make_principal(user.id)

    settings = _settings_historical()
    snapshot = await _run_snapshot_job(session_factory, job_id, settings, include_satellite=True)
    assert snapshot is not None

    async with session_factory() as session:
        response = await simulate(
            session=session,
            principal=principal,
            farm_id=farm.id,
            crop_name="Maize",
            planting_date=date(2027, 4, 1),
            cultivation_mode="rain_fed",
            irrigation_mm=None,
        )

    assert isinstance(response, SimulationResponse)
    assert response.snapshot_id == str(snapshot.id), (
        f"Expected snapshot_id={snapshot.id}, got {response.snapshot_id}"
    )
    assert response.data_mode != "demonstration", (
        f"Expected real data_mode, got 'demonstration'"
    )

    selected = response.selected
    assert selected.crop_name == "Maize"
    assert selected.snapshot_id == str(snapshot.id)

    # At least soil_ph and ndvi_mean should be 'real' (we mocked accepted soil + satellite)
    completeness = selected.input_completeness
    real_fields = [f for f, status in completeness.items() if status == "real"]
    assert len(real_fields) >= 2, (
        f"Expected at least 2 real fields, got: {completeness}"
    )
    assert "soil_ph" in real_fields or "ndvi_mean" in real_fields, (
        f"Expected soil_ph or ndvi_mean to be real, got: {completeness}"
    )

    # Alternatives should be 3
    assert len(response.alternatives) == 3


# ---------------------------------------------------------------------------
# Test 3: Hard exclusion — extreme temperature → suitability_index = 0
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hard_exclusion_extreme_temperature_returns_zero(session_factory):
    """Extreme temperature (> ceiling + 8°C) triggers hard exclusion → index = 0.

    Requirements: 3.3
    """
    from app.domain.snapshot_context import SnapshotContext

    # Pick a crop and compute an extreme temperature
    # Maize heat tolerance ceiling is 35°C; anything > 43°C triggers hard exclusion
    maize = next(c for c in CROP_REGISTER if c.name == "Maize")
    extreme_temp = maize.heat_tolerance_ceiling_c + 9.0  # 44°C

    context = SnapshotContext(
        source="snapshot",
        snapshot_id=str(uuid.uuid4()),
        data_mode="historical_replay",
        temperature_mean_c=extreme_temp,
        temperature_source="test",
        rainfall_total_mm=400.0,
        rainfall_source="test",
        soil_ph=6.5,
        soil_clay_pct=25.0,
        soil_sand_pct=40.0,
        soil_source="test",
        soil_is_modelled=True,
        ndvi_mean=0.5,
        ndvi_source="test",
        vpd_kpa=1.5,
        real_input_fields=frozenset([
            "temperature_mean_c", "rainfall_total_mm", "soil_ph",
            "soil_clay_pct", "soil_sand_pct", "ndvi_mean", "vpd_kpa",
        ]),
        demonstration_input_fields=frozenset(),
    )

    result = score(maize, context)

    assert result.hard_exclusion is True, (
        f"Expected hard_exclusion=True for Maize at {extreme_temp}°C"
    )
    assert result.suitability_index == 0, (
        f"Expected suitability_index=0 under hard exclusion, got {result.suitability_index}"
    )
    assert result.hard_exclusion_reason is not None
    assert str(extreme_temp) in result.hard_exclusion_reason or "Temperature" in result.hard_exclusion_reason


@pytest.mark.asyncio
async def test_hard_exclusion_via_service_all_crops(session_factory):
    """rank_all() with extreme temperature produces at least one hard-excluded crop.

    Requirements: 3.3
    """
    from app.domain.snapshot_context import SnapshotContext

    # Use a temperature so extreme it excludes multiple crops (55°C)
    extreme_context = SnapshotContext(
        source="snapshot",
        snapshot_id=None,
        data_mode="historical_replay",
        temperature_mean_c=55.0,
        temperature_source="test",
        rainfall_total_mm=300.0,
        rainfall_source="test",
        soil_ph=6.5,
        soil_clay_pct=25.0,
        soil_sand_pct=40.0,
        soil_source="test",
        soil_is_modelled=True,
        ndvi_mean=0.5,
        ndvi_source="test",
        vpd_kpa=1.5,
        real_input_fields=frozenset([
            "temperature_mean_c", "rainfall_total_mm", "soil_ph",
            "soil_clay_pct", "soil_sand_pct", "ndvi_mean", "vpd_kpa",
        ]),
        demonstration_input_fields=frozenset(),
    )

    ranked = rank_all(extreme_context)
    excluded = [r for r in ranked if r.hard_exclusion]
    assert len(excluded) > 0, "Expected at least one hard-excluded crop at 55°C"

    for r in excluded:
        assert r.suitability_index == 0
        assert r.label == "Higher caution"


# ---------------------------------------------------------------------------
# Test 4: Missing NDVI → neutral score 65, "environmental_condition" in demo fields
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_ndvi_uses_neutral_score_and_labels_demonstration(session_factory):
    """When satellite is unavailable (NDVI=null), engine uses neutral score 65
    and environmental_condition appears in demonstration_input_fields.

    Requirements: 3.4
    """
    user = await _create_user(session_factory, "missing-ndvi-neutral")
    farm_result = await _create_farm(session_factory, user)
    farm = farm_result.farm
    job_id = farm_result.analysis_job_id
    principal = _make_principal(user.id)

    settings = _settings_historical()
    # Run snapshot job WITHOUT satellite (ndvi unavailable)
    snapshot = await _run_snapshot_job(session_factory, job_id, settings, include_satellite=False)
    assert snapshot is not None
    assert snapshot.satellite is None

    # Build context from snapshot — ndvi_mean should be None
    context = context_from_snapshot(snapshot, planting_month=4, crop_duration=4)
    assert context.ndvi_mean is None, (
        f"Expected ndvi_mean=None when satellite unavailable, got {context.ndvi_mean}"
    )
    assert "ndvi_mean" in context.demonstration_input_fields, (
        f"Expected 'ndvi_mean' in demonstration_input_fields, got: {context.demonstration_input_fields}"
    )

    # Score any crop — environmental_condition component must equal neutral 65
    maize = next(c for c in CROP_REGISTER if c.name == "Maize")
    result = score(maize, context)

    assert result.components.environmental_condition == _NEUTRAL_NDVI_SCORE, (
        f"Expected neutral NDVI score {_NEUTRAL_NDVI_SCORE}, got {result.components.environmental_condition}"
    )
    assert result.input_completeness.get("ndvi_mean") == "demonstration", (
        f"Expected ndvi_mean='demonstration' in completeness, got: {result.input_completeness}"
    )

    # Also verify via the service layer
    async with session_factory() as session:
        response = await simulate(
            session=session,
            principal=principal,
            farm_id=farm.id,
            crop_name="Maize",
            planting_date=date(2027, 4, 1),
            cultivation_mode="rain_fed",
            irrigation_mm=None,
        )

    assert isinstance(response, SimulationResponse)
    assert response.snapshot_id is not None, "Should use real snapshot even with missing NDVI"
    selected = response.selected
    assert selected.input_completeness.get("ndvi_mean") == "demonstration", (
        f"Service response should show ndvi_mean='demonstration': {selected.input_completeness}"
    )
    assert selected.components.environmental_condition == int(round(_NEUTRAL_NDVI_SCORE)), (
        f"Expected neutral NDVI score {int(round(_NEUTRAL_NDVI_SCORE))}, "
        f"got {selected.components.environmental_condition}"
    )


# ---------------------------------------------------------------------------
# Test 5: Cross-user — simulate-crop on another user's farm returns 404
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cross_user_ownership_isolation_raises_not_found(session_factory):
    """POST simulate-crop on another user's farm raises NotFoundError (→ 404).

    Requirements: 6.4
    """
    user_a = await _create_user(session_factory, "crop-owner-a")
    user_b = await _create_user(session_factory, "crop-owner-b")

    farm_result_a = await _create_farm(session_factory, user_a)
    farm_a_id = farm_result_a.farm.id

    # User B tries to simulate on user A's farm
    principal_b = _make_principal(user_b.id)

    async with session_factory() as session:
        with pytest.raises(NotFoundError):
            await simulate(
                session=session,
                principal=principal_b,
                farm_id=farm_a_id,
                crop_name="Maize",
                planting_date=date(2027, 4, 1),
                cultivation_mode="rain_fed",
                irrigation_mm=None,
            )

    # Confirm user A can still access their own farm
    principal_a = _make_principal(user_a.id)
    async with session_factory() as session:
        response = await simulate(
            session=session,
            principal=principal_a,
            farm_id=farm_a_id,
            crop_name="Maize",
            planting_date=date(2027, 4, 1),
            cultivation_mode="rain_fed",
            irrigation_mm=None,
        )

    assert response is not None
    assert response.farm_id == str(farm_a_id)


# ---------------------------------------------------------------------------
# Additional: Verify NotFoundError is also raised for a completely unknown farm_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nonexistent_farm_raises_not_found(session_factory):
    """simulate() with a nonexistent farm_id raises NotFoundError.

    Requirements: 6.4
    """
    user = await _create_user(session_factory, "nonexistent-farm-user")
    principal = _make_principal(user.id)
    fake_farm_id = uuid.uuid4()

    async with session_factory() as session:
        with pytest.raises(NotFoundError):
            await simulate(
                session=session,
                principal=principal,
                farm_id=fake_farm_id,
                crop_name=None,
                planting_date=date(2027, 4, 1),
                cultivation_mode="rain_fed",
                irrigation_mm=None,
            )
