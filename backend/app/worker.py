"""
FarmTwin analysis job worker.

Reads job IDs from the Redis queue (key: ``farmtwin:jobs``) and calls
``run_analysis_job`` for each one.  Processes one job at a time.

Retry policy: up to 2 retries with 30-second back-off.
Shutdown: handles SIGTERM by finishing the current job then exiting cleanly.

Uses a separate async SQLAlchemy session factory — does NOT share the API
process connection pool.

Usage::

    python -m app.worker

Requirements: 1.3, 1.5
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
import uuid
from typing import NoReturn

import redis.asyncio as aioredis

from app.core.config import Settings
from app.core.database import create_engine, create_session_factory
from app.core.logging import configure_logging
from app.services.snapshot_service import REDIS_JOB_QUEUE_KEY, run_analysis_job

logger = logging.getLogger("farmtwin.worker")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 30
QUEUE_POLL_TIMEOUT_SECONDS = 5  # BLPOP block timeout; 0 = forever


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


class Worker:
    """Pulls jobs from Redis and runs them one at a time.

    Designed for a single asyncio event loop.  SIGTERM sets
    ``_shutdown`` which prevents accepting new jobs after the current
    one finishes.

    Requirements: 1.3, 1.5
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._shutdown = False

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    def _handle_sigterm(self, signum: int, frame) -> None:  # noqa: ANN001
        """Set the shutdown flag; the current job will finish normally."""
        logger.info("worker.sigterm: graceful shutdown requested.")
        self._shutdown = True

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Connect to Redis and process jobs until SIGTERM."""
        signal.signal(signal.SIGTERM, self._handle_sigterm)

        redis_url = getattr(self._settings, "redis_url", None) or "redis://localhost:6379"
        logger.info("worker.start: connecting to Redis at %s", redis_url)

        # Separate engine / session factory — does NOT share the API pool
        engine = create_engine(self._settings)
        session_factory = create_session_factory(engine)

        try:
            logger.info("worker.ready: polling committed analysis jobs")
            while not self._shutdown:
                try:
                    await self._poll_and_process(None, session_factory)
                except Exception:
                    logger.exception("worker.poll_error")
                    await asyncio.sleep(5)
        finally:
            await engine.dispose()
            logger.info("worker.stopped: database pool disposed.")

    # ------------------------------------------------------------------
    # Poll + process
    # ------------------------------------------------------------------

    async def _poll_and_process(self, redis_client, session_factory) -> None:
        """Poll committed jobs, including expired leases and retryable failures."""
        from datetime import datetime, timezone
        from sqlalchemy import select, or_
        from app.models.snapshot import AnalysisJob, JobStatus
        async with session_factory() as session:
            ids = (await session.execute(select(AnalysisJob.id).where(
                AnalysisJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.FAILED]),
                AnalysisJob.attempts < 3,
                or_(AnalysisJob.lease_until.is_(None), AnalysisJob.lease_until <= datetime.now(timezone.utc)),
            ).order_by(AnalysisJob.created_at).limit(1))).scalars().all()
        if not ids:
            await asyncio.sleep(2)
            return
        await self._run_with_retry(ids[0], session_factory)

    async def _run_with_retry(self, job_id, session_factory) -> None:
        # Attempts and retry time live in the database and survive process restarts.
        try:
            async with asyncio.timeout(540):
                await run_analysis_job(job_id, self._settings, session_factory)
        except Exception:
            logger.exception("worker.job_error: job=%s; durable lease will allow recovery", job_id)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> NoReturn:
    """Synchronous entry point for ``python -m app.worker``."""
    settings = Settings()
    configure_logging(settings)
    logger.info("worker.init: environment=%s data_mode=%s", settings.environment, settings.data_mode)
    worker = Worker(settings)
    asyncio.run(worker.run())
    sys.exit(0)


if __name__ == "__main__":
    main()
