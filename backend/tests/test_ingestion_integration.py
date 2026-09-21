"""
Integration tests for the full Conduit fixture ingestion pipeline.

Task 9 checkpoint: runs ingest_fixture() against the 191-record fixture on
the test database and verifies end-to-end correctness.

Requirements: 1.2, 1.3, 6.2, 10.3, 12.2, 12.3
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import DataMode, Settings
from app.models.conduit import (
    DailyAggregate,
    HourlyAggregate,
    IngestionRun,
    IngestionStatus,
    NormalizedObservation,
    QualityFlag,
    Station,
)
from app.services.ingestion import IngestionConfigError, ingest_fixture

# ---------------------------------------------------------------------------
# Fixture path helpers
# ---------------------------------------------------------------------------

# tests/ → backend/ → farmtwin/ → repo root
_REPO_ROOT = Path(__file__).parent.parent.parent.parent
_FIXTURE_PATH = _REPO_ROOT / "data" / "samples" / "weather.json"

# Expected record count for the full fixture
EXPECTED_ACCEPTED = 191


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

def _historical_settings() -> Settings:
    """Return settings appropriate for fixture ingestion (non-live)."""
    # We build a minimal Settings override from the test .env, overriding
    # data_mode to historical_replay.
    env_path = Path(__file__).parent.parent / ".env"
    overrides: dict = {
        "DATA_MODE": "historical_replay",
    }
    return Settings(_env_file=str(env_path), **overrides)


def _live_settings() -> Settings:
    """Return settings with DATA_MODE=live to test the refusal gate."""
    env_path = Path(__file__).parent.parent / ".env"
    # local_demo restrictions prevent live mode together with local_demo auth.
    # Read the base .env and force live mode only.
    import dotenv  # type: ignore[import-untyped]
    env_vars = dotenv.dotenv_values(str(env_path))
    env_vars["DATA_MODE"] = "live"
    # If auth mode is local_demo, switch to a stub oidc to avoid demo validation conflict.
    # The refusal happens before auth is checked in the service, so we only
    # need a valid Settings object that has data_mode=live.
    #
    # Simplest approach: patch environment variables for the Settings constructor.
    import os
    original = {}
    try:
        for k, v in env_vars.items():
            if k and v is not None:
                original[k] = os.environ.get(k)
                os.environ[k] = v
        os.environ["DATA_MODE"] = "live"
        # Attempt to build Settings — may fail for auth reasons in CI.
        # That is fine; we catch the config error in the test.
        try:
            s = Settings()
            return s
        except Exception:
            # If Settings itself can't be constructed (e.g. OIDC fields
            # missing), raise a sentinel so the caller can skip cleanly.
            raise
    finally:
        for k, orig_v in original.items():
            if orig_v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = orig_v


# ---------------------------------------------------------------------------
# Test: first ingestion inserts 191 rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_ingestion_accepted_count(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Running ingest_fixture once on the 191-record fixture must produce
    exactly 191 accepted NormalizedObservation rows.

    Requirements: 1.2, 10.3
    """
    settings = _historical_settings()
    run = await ingest_fixture(session_factory, settings, _FIXTURE_PATH)

    assert run.status == IngestionStatus.COMPLETED
    assert run.accepted_count == EXPECTED_ACCEPTED
    assert run.rejected_count == 0
    assert run.duplicate_count == 0

    # Verify the DB count matches
    async with session_factory() as session:
        count = await session.scalar(
            select(func.count()).select_from(NormalizedObservation)
        )
    assert count == EXPECTED_ACCEPTED


# ---------------------------------------------------------------------------
# Test: second ingestion produces zero new rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_ingestion_zero_duplicates(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Ingesting the same fixture a second time must produce zero new rows.
    The second run is detected via checksum and returns duplicate_run status,
    or via per-record deduplication if a fresh run is started.

    Requirements: 6.2, 10.5, 12.2
    """
    settings = _historical_settings()

    # First run
    first_run = await ingest_fixture(session_factory, settings, _FIXTURE_PATH)
    assert first_run.status == IngestionStatus.COMPLETED
    assert first_run.accepted_count == EXPECTED_ACCEPTED

    # Second run — same fixture, same checksum
    second_run = await ingest_fixture(session_factory, settings, _FIXTURE_PATH)

    # Either the checksum short-circuit returns duplicate_run,
    # or (if run record was somehow not found) deduplication skips all rows.
    # Both are valid; the invariant is zero new rows.
    assert second_run.status in (IngestionStatus.DUPLICATE_RUN, IngestionStatus.COMPLETED)

    if second_run.status == IngestionStatus.COMPLETED:
        # Dedup path: all 191 records skipped as duplicates
        assert second_run.duplicate_count == EXPECTED_ACCEPTED
        assert second_run.accepted_count == 0

    # DB row count must still be exactly 191 (no duplicates stored)
    async with session_factory() as session:
        count = await session.scalar(
            select(func.count()).select_from(NormalizedObservation)
        )
    assert count == EXPECTED_ACCEPTED


# ---------------------------------------------------------------------------
# Test: aggregates are created
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aggregates_created_after_ingestion(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    After a successful ingestion the hourly and daily aggregate tables
    must both be non-empty.

    Requirements: 1.2, 10.3
    """
    settings = _historical_settings()
    await ingest_fixture(session_factory, settings, _FIXTURE_PATH)

    async with session_factory() as session:
        hourly_count = await session.scalar(
            select(func.count()).select_from(HourlyAggregate)
        )
        daily_count = await session.scalar(
            select(func.count()).select_from(DailyAggregate)
        )

    assert hourly_count is not None and hourly_count > 0, (
        f"Expected hourly aggregates, got {hourly_count}"
    )
    assert daily_count is not None and daily_count > 0, (
        f"Expected daily aggregates, got {daily_count}"
    )


# ---------------------------------------------------------------------------
# Test: VPD non-null for accepted temperature + humidity observations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vpd_non_null_for_accepted_inputs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Every NormalizedObservation that has an accepted temperature_consensus
    AND accepted humidity_sht must have a non-null vpd_kpa value.

    Requirements: 1.2, 10.3 (via requirement 9.1)
    """
    settings = _historical_settings()
    await ingest_fixture(session_factory, settings, _FIXTURE_PATH)

    async with session_factory() as session:
        # Fetch observations with both temperature and humidity accepted
        result = await session.execute(
            select(NormalizedObservation).where(
                NormalizedObservation.temperature_consensus_quality.in_(
                    [QualityFlag.ACCEPTED, QualityFlag.SINGLE_CHANNEL]
                ),
                NormalizedObservation.humidity_sht_quality == QualityFlag.ACCEPTED,
            )
        )
        qualifying_obs = result.scalars().all()

    assert len(qualifying_obs) > 0, (
        "Expected at least some observations with accepted T and RH"
    )

    null_vpd = [
        obs for obs in qualifying_obs
        if obs.vpd_kpa is None
    ]
    assert null_vpd == [], (
        f"{len(null_vpd)} observations have accepted T+RH but null vpd_kpa"
    )


# ---------------------------------------------------------------------------
# Test: data_mode is historical_replay for all observations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_observations_labeled_historical_replay(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    All NormalizedObservation rows produced from the fixture must carry
    data_mode = 'historical_replay'.

    Requirements: 1.2
    """
    settings = _historical_settings()
    await ingest_fixture(session_factory, settings, _FIXTURE_PATH)

    async with session_factory() as session:
        non_replay = await session.scalar(
            select(func.count()).select_from(NormalizedObservation).where(
                NormalizedObservation.data_mode != "historical_replay"
            )
        )
    assert non_replay == 0, (
        f"{non_replay} observations not labeled historical_replay"
    )


# ---------------------------------------------------------------------------
# Test: management command refuses DATA_MODE=live
# ---------------------------------------------------------------------------


def test_management_command_refuses_live_mode() -> None:
    """
    Running the ingest-fixture management command with DATA_MODE=live must
    exit with a nonzero return code.

    Requirements: 1.3, 12.3
    """
    backend_dir = Path(__file__).parent.parent
    env = os.environ.copy()
    env["DATA_MODE"] = "live"

    result = subprocess.run(
        [sys.executable, "-m", "app.db.management", "ingest-fixture"],
        cwd=str(backend_dir),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert result.returncode != 0, (
        "Expected nonzero exit code when DATA_MODE=live, "
        f"got {result.returncode}. stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    # The error output should mention the config issue
    combined = (result.stdout + result.stderr).lower()
    assert "live" in combined or "config" in combined or "error" in combined, (
        f"Expected error message mentioning 'live' or 'config', got: {combined!r}"
    )


# ---------------------------------------------------------------------------
# Test: service-level refusal when DATA_MODE=live
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_fixture_service_refuses_live_mode(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    ingest_fixture() must raise IngestionConfigError when DATA_MODE is live,
    without touching the database.

    Requirements: 1.3, 12.3
    """
    # Build a settings object where only data_mode=live is patched.
    # We use the existing historical settings and override just the data_mode field.
    base_settings = _historical_settings()

    # Create a mock-like settings with data_mode=live using model_copy
    live_settings = base_settings.model_copy(update={"data_mode": DataMode.LIVE})

    with pytest.raises(IngestionConfigError, match="live"):
        await ingest_fixture(session_factory, live_settings, _FIXTURE_PATH)

    # DB should be untouched
    async with session_factory() as session:
        count = await session.scalar(
            select(func.count()).select_from(NormalizedObservation)
        )
    assert count == 0
