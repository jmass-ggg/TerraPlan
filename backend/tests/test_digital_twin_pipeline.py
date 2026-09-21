"""
Focused regression tests for the Digital Twin pipeline bugs fixed in this change.

Tests cover:
1. Weather provider success
2. Weather transient 503 followed by success (retry logic)
3. Weather persistent provider failure
4. STAC item using resolution-suffixed keys (B04_10m / B08_10m)
5. STAC item using semantic red/nir keys
6. STAC item without required bands
7. Soil completion marks job stage correctly
8. Terrain completion marks job stage correctly
9. One provider failure does not leave job running forever
10. Optional Conduit unavailable does not block job finalization
11. Partial Digital Twin returns successful stage data
12. GET /digital-twin does not enqueue duplicate jobs
13. New job does not show stale data from previous snapshot

Requirements: 1.3, 2.4, 4.1, 5.5, 6.5, 7.1, 7.2, 10.1
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import httpx
import pytest
from shapely.geometry import Polygon

from app.data.providers import weather as weather_provider
from app.data.providers import satellite as satellite_provider
from app.data.providers.base import (
    EVIDENCE_ACCEPTED,
    EVIDENCE_UNAVAILABLE,
    ProviderResult,
)
from app.data.providers.satellite import (
    _find_asset_href,
    _BAND_ASSET_KEYS,
)
from app.models.snapshot import AnalysisJob, AnalysisSnapshot, JobStatus
from app.services.snapshot_service import run_analysis_job


# ---------------------------------------------------------------------------
# Settings helper
# ---------------------------------------------------------------------------

def _live_settings():
    from app.core.config import Settings
    env_path = Path(__file__).parent.parent / ".env"
    return Settings(_env_file=str(env_path), data_mode="live")


def _historical_settings():
    from app.core.config import Settings
    env_path = Path(__file__).parent.parent / ".env"
    return Settings(_env_file=str(env_path), data_mode="historical_replay")


# ---------------------------------------------------------------------------
# Farm fixtures
# ---------------------------------------------------------------------------

BOUNDARY = {
    "type": "Polygon",
    "coordinates": [[[36.80, -1.30], [36.805, -1.30], [36.805, -1.295], [36.80, -1.30]]],
}


async def _make_user(session_factory, subject: str):
    from app.models.user import User
    async with session_factory() as session:
        user = User(id=uuid.uuid4(), issuer="dt-pipeline-tests", subject=subject)
        session.add(user)
        await session.commit()
        return user


async def _make_farm(session_factory, user):
    from app.api.v1.farm_schemas import FarmCreate
    from app.services.farm_service import FarmService
    async with session_factory() as session:
        svc = FarmService(session, user.id)
        result = await svc.create_farm_request(FarmCreate(name="DT Test Farm", geometry=BOUNDARY))
        return result


def _unavail(source: str = "test") -> ProviderResult:
    return ProviderResult(payload=None, evidence_status=EVIDENCE_UNAVAILABLE, error_message=f"{source} unavailable")


def _accepted_weather() -> ProviderResult:
    return ProviderResult(
        payload={"fields": {"temperature_2m": {"value": 22.1, "unit": "celsius", "quality": "accepted",
                 "source": "open-meteo", "acquired_at": "2026-09-08T06:00Z",
                 "retrieved_at": "2026-09-08T09:00Z", "data_mode": "live", "resolution_m": 11000}}},
        evidence_status=EVIDENCE_ACCEPTED, error_message=None,
    )


def _accepted_soil() -> ProviderResult:
    return ProviderResult(
        payload={"depths": {"0-5cm": {}}, "source_resolution_m": 250, "modelled_estimate": True},
        evidence_status=EVIDENCE_ACCEPTED, error_message=None,
    )


def _accepted_terrain() -> ProviderResult:
    return ProviderResult(
        payload={"mean_elevation_m": {"value": 1640.0, "unit": "metres"}, "flood_probability": None},
        evidence_status=EVIDENCE_ACCEPTED, error_message=None,
    )


def _accepted_climate() -> ProviderResult:
    return ProviderResult(
        payload={"baseline_period": "1991-2020"},
        evidence_status=EVIDENCE_ACCEPTED, error_message=None,
    )


# ===========================================================================
# 1. Weather provider success
# ===========================================================================

@pytest.mark.asyncio
async def test_weather_provider_success():
    """Weather fetch returns accepted result on 200."""
    mock_data = {
        "current": {"time": "2026-09-08T06:00", "temperature_2m": 22.5, "precipitation": 0.0,
                    "relative_humidity_2m": 70.0, "wind_speed_10m": 2.1,
                    "wind_direction_10m": 180.0, "cloud_cover": 30.0},
        "current_units": {"temperature_2m": "°C", "precipitation": "mm",
                          "relative_humidity_2m": "%", "wind_speed_10m": "m/s",
                          "wind_direction_10m": "°", "cloud_cover": "%"},
        "hourly": {}, "hourly_units": {}, "daily": {}, "daily_units": {},
    }
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = mock_data

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_cm):
        result = await weather_provider.fetch(-2.05, 37.40, data_mode="live")

    assert result.evidence_status == EVIDENCE_ACCEPTED
    assert result.payload is not None
    assert result.payload["fields"]["temperature_2m"]["value"] == 22.5


# ===========================================================================
# 2. Weather transient 503 followed by success (retry logic)
# ===========================================================================

@pytest.mark.asyncio
async def test_weather_transient_503_then_success():
    """Weather retries on 503 and eventually succeeds.

    First call returns 503, second returns 200 with valid data.
    """
    success_data = {
        "current": {"time": "2026-09-08T06:00", "temperature_2m": 19.0,
                    "precipitation": 0.5, "relative_humidity_2m": 80.0,
                    "wind_speed_10m": 1.5, "wind_direction_10m": 90.0, "cloud_cover": 50.0},
        "current_units": {"temperature_2m": "°C", "precipitation": "mm",
                          "relative_humidity_2m": "%", "wind_speed_10m": "m/s",
                          "wind_direction_10m": "°", "cloud_cover": "%"},
        "hourly": {}, "hourly_units": {}, "daily": {}, "daily_units": {},
    }

    fail_resp = MagicMock(spec=httpx.Response)
    fail_resp.status_code = 503
    fail_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "503", request=MagicMock(), response=fail_resp
    )

    ok_resp = MagicMock(spec=httpx.Response)
    ok_resp.status_code = 200
    ok_resp.raise_for_status = MagicMock()
    ok_resp.json.return_value = success_data

    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=[fail_resp, ok_resp])
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_cm):
        with patch("asyncio.sleep", new=AsyncMock()):  # don't actually wait
            result = await weather_provider.fetch(-2.05, 37.40, data_mode="live")

    assert result.evidence_status == EVIDENCE_ACCEPTED
    assert result.payload["fields"]["temperature_2m"]["value"] == 19.0
    assert mock_client.get.call_count == 2


# ===========================================================================
# 3. Weather persistent provider failure
# ===========================================================================

@pytest.mark.asyncio
async def test_weather_persistent_503_marks_unavailable():
    """Weather returns unavailable after exhausting all retries on 503."""
    fail_resp = MagicMock(spec=httpx.Response)
    fail_resp.status_code = 503
    fail_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "503", request=MagicMock(), response=fail_resp
    )

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=fail_resp)
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_cm):
        with patch("asyncio.sleep", new=AsyncMock()):
            result = await weather_provider.fetch(-2.05, 37.40, data_mode="live")

    assert result.evidence_status == EVIDENCE_UNAVAILABLE
    assert "503" in (result.error_message or "")
    # Must have retried _MAX_RETRIES times
    assert mock_client.get.call_count == weather_provider._MAX_RETRIES


# ===========================================================================
# 4. STAC item with resolution-suffixed keys (B04_10m / B08_10m)
# ===========================================================================

def test_find_asset_href_resolution_suffixed_keys():
    """_find_asset_href finds B04 via B04_10m (Copernicus Data Space pattern).

    The asset has an alternate.https.href which must be preferred.
    """
    assets = {
        "B04_10m": {
            "href": "s3://eodata/path/B04_10m.jp2",
            "alternate": {
                "https": {
                    "href": "https://download.dataspace.copernicus.eu/path/B04_10m.jp2"
                }
            },
        }
    }
    href = _find_asset_href(assets, _BAND_ASSET_KEYS["B04"])
    assert href == "https://download.dataspace.copernicus.eu/path/B04_10m.jp2"


# ===========================================================================
# 5. STAC item using semantic red/nir keys
# ===========================================================================

def test_find_asset_href_semantic_red_key():
    """_find_asset_href resolves 'red' semantic key when B04_10m is absent."""
    assets = {
        "red": {
            "href": "https://example.com/red_band.tif",
        }
    }
    href = _find_asset_href(assets, _BAND_ASSET_KEYS["B04"])
    assert href == "https://example.com/red_band.tif"


def test_find_asset_href_semantic_nir_key():
    """_find_asset_href resolves 'nir' semantic key when B08_10m is absent."""
    assets = {
        "nir": {
            "href": "https://example.com/nir_band.tif",
        }
    }
    href = _find_asset_href(assets, _BAND_ASSET_KEYS["B08"])
    assert href == "https://example.com/nir_band.tif"


# ===========================================================================
# 6. STAC item without required bands → unavailable with diagnostic
# ===========================================================================

@pytest.mark.asyncio
async def test_stac_missing_bands_returns_unavailable_with_diagnostic():
    """If B04/B08 not found in assets, satellite returns unavailable with available keys logged."""
    fake_scene = {
        "id": "TEST_SCENE_NO_BANDS",
        "properties": {
            "datetime": "2026-08-25T07:36:09Z",
            "eo:cloud_cover": 5.0,
        },
        "assets": {
            # Only has TCI, no band assets
            "TCI_10m": {"href": "https://example.com/tci.jp2"},
        },
    }

    with patch(
        "app.data.providers.satellite._search_scenes",
        new=AsyncMock(return_value=[fake_scene]),
    ), patch(
        "app.data.providers.satellite._fetch_cdse_token",
        new=AsyncMock(return_value=("dummy-token-for-test", None)),
    ):
        result = await satellite_provider.fetch(
            farm_polygon=Polygon([
                (36.80, -1.30), (36.805, -1.30),
                (36.805, -1.295), (36.80, -1.30),
            ]),
            data_mode="live",
            cdse_username="test@example.com",
            cdse_password="testpass",
        )

    assert result.evidence_status == EVIDENCE_UNAVAILABLE
    assert result.error_message is not None
    # Error message must include available asset keys for diagnosis
    assert "TCI_10m" in result.error_message or "Available asset keys" in result.error_message


# ===========================================================================
# 7 & 8. Soil and terrain completion mark stage correctly
# ===========================================================================

@pytest.mark.asyncio
async def test_soil_and_terrain_stages_completed_in_finalized_snapshot(session_factory):
    """After a run where soil+terrain succeed, job.stages reflects completed for both."""
    user = await _make_user(session_factory, "stage-soil-terrain")
    farm_result = await _make_farm(session_factory, user)
    job_id = farm_result.analysis_job_id
    assert job_id is not None

    settings = _live_settings()

    with (
        patch("app.data.providers.weather.fetch", new=AsyncMock(return_value=_unavail("weather"))),
        patch("app.data.providers.climate.fetch", new=AsyncMock(return_value=_unavail("climate"))),
        patch("app.data.providers.satellite.fetch", new=AsyncMock(return_value=_unavail("satellite"))),
        patch("app.data.providers.soil.fetch", new=AsyncMock(return_value=_accepted_soil())),
        patch("app.data.providers.terrain.fetch", new=AsyncMock(return_value=_accepted_terrain())),
    ):
        snapshot = await run_analysis_job(job_id, settings, session_factory)

    assert snapshot is not None

    async with session_factory() as session:
        job = await session.get(AnalysisJob, job_id)

    assert job.status == JobStatus.COMPLETED
    assert job.stages["soil"]["status"] == "completed"
    assert job.stages["terrain"]["status"] == "completed"
    # Failed providers must be explicitly recorded
    assert job.stages["weather"]["status"] == "failed"


# ===========================================================================
# 9. One provider failure does not leave job running forever
# ===========================================================================

@pytest.mark.asyncio
async def test_failed_provider_does_not_leave_job_running(session_factory):
    """A single provider failure must not leave the job in RUNNING state.

    The job must reach COMPLETED (with partial evidence) rather than hanging.
    """
    user = await _make_user(session_factory, "job-not-hanging")
    farm_result = await _make_farm(session_factory, user)
    job_id = farm_result.analysis_job_id

    settings = _live_settings()

    with (
        patch("app.data.providers.weather.fetch", new=AsyncMock(return_value=_unavail("weather"))),
        patch("app.data.providers.climate.fetch", new=AsyncMock(return_value=_accepted_climate())),
        patch("app.data.providers.satellite.fetch", new=AsyncMock(return_value=_unavail("satellite"))),
        patch("app.data.providers.soil.fetch", new=AsyncMock(return_value=_accepted_soil())),
        patch("app.data.providers.terrain.fetch", new=AsyncMock(return_value=_accepted_terrain())),
    ):
        snapshot = await run_analysis_job(job_id, settings, session_factory)

    assert snapshot is not None

    async with session_factory() as session:
        job = await session.get(AnalysisJob, job_id)

    # Job must NOT be running — must be terminal
    assert job.status in (JobStatus.COMPLETED, JobStatus.FAILED)
    assert job.status == JobStatus.COMPLETED  # snapshot was created with partial evidence


# ===========================================================================
# 10. Optional Conduit unavailable does not block job finalization
# ===========================================================================

@pytest.mark.asyncio
async def test_conduit_unavailable_does_not_block_job_completion(session_factory):
    """Conduit being unavailable (no stations) must not prevent job completion."""
    user = await _make_user(session_factory, "conduit-unavail")
    farm_result = await _make_farm(session_factory, user)
    job_id = farm_result.analysis_job_id

    settings = _live_settings()

    # No station in DB → conduit returns unavailable
    with (
        patch("app.data.providers.weather.fetch", new=AsyncMock(return_value=_accepted_weather())),
        patch("app.data.providers.climate.fetch", new=AsyncMock(return_value=_accepted_climate())),
        patch("app.data.providers.satellite.fetch", new=AsyncMock(return_value=_unavail("satellite"))),
        patch("app.data.providers.soil.fetch", new=AsyncMock(return_value=_accepted_soil())),
        patch("app.data.providers.terrain.fetch", new=AsyncMock(return_value=_accepted_terrain())),
    ):
        snapshot = await run_analysis_job(job_id, settings, session_factory)

    assert snapshot is not None

    async with session_factory() as session:
        job = await session.get(AnalysisJob, job_id)

    assert job.status == JobStatus.COMPLETED
    assert snapshot.evidence_statuses["conduit"] == EVIDENCE_UNAVAILABLE
    # Conduit unavailable → conduit payload is null (no fabricated data)
    assert snapshot.conduit is None


# ===========================================================================
# 11. Partial Digital Twin returns successful stage data
# ===========================================================================

@pytest.mark.asyncio
async def test_partial_snapshot_preserves_successful_stage_data(session_factory):
    """With weather+satellite failing, soil and terrain data must be in the snapshot."""
    user = await _make_user(session_factory, "partial-data")
    farm_result = await _make_farm(session_factory, user)
    job_id = farm_result.analysis_job_id

    settings = _live_settings()

    with (
        patch("app.data.providers.weather.fetch", new=AsyncMock(return_value=_unavail("weather"))),
        patch("app.data.providers.climate.fetch", new=AsyncMock(return_value=_unavail("climate"))),
        patch("app.data.providers.satellite.fetch", new=AsyncMock(return_value=_unavail("satellite"))),
        patch("app.data.providers.soil.fetch", new=AsyncMock(return_value=_accepted_soil())),
        patch("app.data.providers.terrain.fetch", new=AsyncMock(return_value=_accepted_terrain())),
    ):
        snapshot = await run_analysis_job(job_id, settings, session_factory)

    assert snapshot is not None
    # Successful providers must be present
    assert snapshot.soil is not None
    assert snapshot.terrain is not None
    assert snapshot.evidence_statuses["soil"] == EVIDENCE_ACCEPTED
    assert snapshot.evidence_statuses["terrain"] == EVIDENCE_ACCEPTED
    # Failed providers must be null
    assert snapshot.weather is None
    assert snapshot.satellite is None
    assert snapshot.evidence_statuses["weather"] == EVIDENCE_UNAVAILABLE
    assert snapshot.evidence_statuses["satellite"] == EVIDENCE_UNAVAILABLE


# ===========================================================================
# 12. GET /digital-twin does not enqueue duplicate jobs
# ===========================================================================

@pytest.mark.asyncio
async def test_get_digital_twin_does_not_enqueue_duplicate_jobs(session_factory):
    """enqueue_analysis_job returns the existing active job rather than creating a new one."""
    from app.services.snapshot_service import enqueue_analysis_job
    user = await _make_user(session_factory, "no-duplicate-job")
    farm_result = await _make_farm(session_factory, user)
    farm_id = farm_result.farm.id
    job_id_1 = farm_result.analysis_job_id

    # Attempt to enqueue again for the same farm + revision
    async with session_factory() as session:
        job2 = await enqueue_analysis_job(farm_id, 1, session)
        await session.commit()

    # Must return the existing job, not a new one
    assert job2.id == job_id_1

    # Verify only one job exists for this farm
    from sqlalchemy import select, func
    async with session_factory() as session:
        count = await session.scalar(
            select(func.count()).select_from(AnalysisJob).where(
                AnalysisJob.farm_id == farm_id,
                AnalysisJob.status.in_(["queued", "running"]),
            )
        )
    assert count == 1


# ===========================================================================
# 13. New job does not show stale data from previous snapshot
# ===========================================================================

@pytest.mark.asyncio
async def test_new_job_previous_snapshot_superseded(session_factory):
    """After a new job completes, GET /digital-twin returns the new snapshot.

    The twin API endpoint returns the most recent snapshot by created_at.
    A second run must produce a different snapshot that supersedes the first.
    """
    from app.services.snapshot_service import enqueue_analysis_job
    user = await _make_user(session_factory, "supersede-snapshot")
    farm_result = await _make_farm(session_factory, user)
    farm_id = farm_result.farm.id
    job_id_1 = farm_result.analysis_job_id

    settings = _live_settings()

    def _patched():
        return (
            patch("app.data.providers.weather.fetch", new=AsyncMock(return_value=_accepted_weather())),
            patch("app.data.providers.climate.fetch", new=AsyncMock(return_value=_unavail("climate"))),
            patch("app.data.providers.satellite.fetch", new=AsyncMock(return_value=_unavail("satellite"))),
            patch("app.data.providers.soil.fetch", new=AsyncMock(return_value=_unavail("soil"))),
            patch("app.data.providers.terrain.fetch", new=AsyncMock(return_value=_unavail("terrain"))),
        )

    # First job
    with _patched()[0], _patched()[1], _patched()[2], _patched()[3], _patched()[4]:
        snap1 = await run_analysis_job(job_id_1, settings, session_factory)
    assert snap1 is not None

    # Force-create a second job (bypass the active-job check by
    # first marking job 1 completed, which already happened above)
    async with session_factory() as session:
        job2 = await enqueue_analysis_job(farm_id, 1, session)
        await session.commit()
    job_id_2 = job2.id
    # Distinct IDs (job1 completed, so a new one is created)
    assert job_id_2 != job_id_1

    with _patched()[0], _patched()[1], _patched()[2], _patched()[3], _patched()[4]:
        snap2 = await run_analysis_job(job_id_2, settings, session_factory)
    assert snap2 is not None
    assert snap2.id != snap1.id

    # The digital-twin endpoint returns the most recent snapshot (by created_at)
    from sqlalchemy import select, desc
    async with session_factory() as session:
        result = await session.execute(
            select(AnalysisSnapshot)
            .where(
                AnalysisSnapshot.farm_id == farm_id,
                AnalysisSnapshot.geometry_revision == 1,
            )
            .order_by(desc(AnalysisSnapshot.created_at))
            .limit(1)
        )
        latest = result.scalar_one_or_none()

    assert latest is not None
    assert latest.id == snap2.id, "GET /digital-twin must return the most recent snapshot"
