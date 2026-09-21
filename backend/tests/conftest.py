"""
Shared test fixtures for FarmTwin backend tests.
"""

import os
from typing import AsyncGenerator

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models.base import Base


# Test database URL from environment or default
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://farmtwin_test:test_password@localhost:5434/farmtwin_test",
)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    """
    Create test database engine.
    
    Connections are pooled only within a test's event loop. The pool is
    disposed at each test boundary so asyncpg connections never cross loops.
    """
    test_engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
    )
    
    # Drop all tables using CASCADE to handle circular FKs
    # (e.g. analysis_jobs.snapshot_id ↔ analysis_snapshots.job_id)
    # We cannot drop the schema because that removes PostGIS; instead drop
    # application tables in dependency order.
    async with test_engine.begin() as conn:
        await conn.execute(
            text("""
                DO $$ DECLARE
                    r RECORD;
                BEGIN
                    FOR r IN (
                        SELECT tablename FROM pg_tables
                        WHERE schemaname = 'public'
                        AND tablename NOT IN ('spatial_ref_sys')
                    ) LOOP
                        EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE';
                    END LOOP;
                END $$;
            """)
        )
        await conn.run_sync(Base.metadata.create_all)
    await test_engine.dispose()
    
    yield test_engine
    
    # Cleanup — drop all application tables (leave PostGIS intact)
    async with test_engine.begin() as conn:
        await conn.execute(
            text("""
                DO $$ DECLARE
                    r RECORD;
                BEGIN
                    FOR r IN (
                        SELECT tablename FROM pg_tables
                        WHERE schemaname = 'public'
                        AND tablename NOT IN ('spatial_ref_sys')
                    ) LOOP
                        EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE';
                    END LOOP;
                END $$;
            """)
        )
    
    await test_engine.dispose()


@pytest_asyncio.fixture
async def session_factory(
    engine: AsyncEngine,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """
    Create session factory for tests.
    
    Each test gets a clean session factory.
    """
    factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )

    # Tests intentionally exercise real service commits, often through several
    # independent sessions.  A transaction wrapped around only one fixture
    # session cannot isolate those writes and is invalidated by fixture commits.
    # Reset all application tables at the test boundary instead.
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE TABLE farm_geometry_revisions, farms, users, "
                "installation_metadata RESTART IDENTITY CASCADE"
            )
        )
        # Conduit pipeline tables (added in Phase 2)
        try:
            await connection.execute(
                text(
                    "TRUNCATE TABLE daily_aggregates, hourly_aggregates, "
                    "normalized_observations, ingestion_runs, stations "
                    "RESTART IDENTITY CASCADE"
                )
            )
        except Exception:
            pass  # Tables may not exist in all test environments
        # Phase 5 snapshot tables
        try:
            await connection.execute(
                text(
                    "TRUNCATE TABLE analysis_snapshots, analysis_jobs "
                    "RESTART IDENTITY CASCADE"
                )
            )
        except Exception:
            pass  # Tables may not exist in all test environments
        # Phase 7 action completions table
        try:
            await connection.execute(
                text("TRUNCATE TABLE action_completions RESTART IDENTITY CASCADE")
            )
        except Exception:
            pass  # Tables may not exist in all test environments
        # Phase 9 saved scenarios table
        try:
            await connection.execute(
                text("TRUNCATE TABLE saved_scenarios RESTART IDENTITY CASCADE")
            )
        except Exception:
            pass  # Tables may not exist in all test environments

    try:
        yield factory
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE TABLE farm_geometry_revisions, farms, users, "
                    "installation_metadata RESTART IDENTITY CASCADE"
                )
            )
            # Conduit pipeline tables (added in Phase 2)
            try:
                await connection.execute(
                    text(
                        "TRUNCATE TABLE daily_aggregates, hourly_aggregates, "
                        "normalized_observations, ingestion_runs, stations "
                        "RESTART IDENTITY CASCADE"
                    )
                )
            except Exception:
                pass  # Tables may not exist in all test environments
            # Phase 5 snapshot tables
            try:
                await connection.execute(
                    text(
                        "TRUNCATE TABLE analysis_snapshots, analysis_jobs "
                        "RESTART IDENTITY CASCADE"
                    )
                )
            except Exception:
                pass  # Tables may not exist in all test environments
            # Phase 7 action completions table
            try:
                await connection.execute(
                    text("TRUNCATE TABLE action_completions RESTART IDENTITY CASCADE")
                )
            except Exception:
                pass  # Tables may not exist in all test environments
            # Phase 9 saved scenarios table
            try:
                await connection.execute(
                    text("TRUNCATE TABLE saved_scenarios RESTART IDENTITY CASCADE")
                )
            except Exception:
                pass  # Tables may not exist in all test environments
        # pytest-asyncio gives each test a function-scoped loop. Disposing here
        # prevents pooled asyncpg connections from being reused by another loop.
        await engine.dispose()


@pytest_asyncio.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """
    Create a test database session with automatic rollback/cleanup.
    """
    async with session_factory() as test_session:
        try:
            yield test_session
        finally:
            await test_session.rollback()
