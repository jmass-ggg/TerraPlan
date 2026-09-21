"""
Tests for local demo setup command.

Requirements: 1.6, 4.3, 12.2

Tests verify:
- Idempotency (can run multiple times)
- Refuses authenticated/live databases
- Doesn't mix demo records with real-user storage
- Preserves fixture timestamps
- All demo responses remain labeled non-live after restart
"""

from datetime import datetime, timezone
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import AuthMode, DataMode, Environment, Settings
from app.core.security import DEMO_ISSUER, DEMO_SUBJECT, DEMO_USER_UUID
from app.db.demo_setup import (
    check_installation_marker,
    create_demo_user,
    initialize_demo_marker,
    seed_demo_farms,
    setup_demo_database,
)
from app.models.farm import Farm, FarmGeometryRevision
from app.models.user import InstallationMetadata, InstallationMode, User


@pytest.mark.asyncio
async def test_initialize_demo_marker_creates_marker(session: AsyncSession):
    """Test that initialize_demo_marker creates the marker"""
    # Verify no marker exists
    existing_mode = await check_installation_marker(session)
    assert existing_mode is None
    
    # Initialize marker
    await initialize_demo_marker(session)
    
    # Verify marker was created
    result = await session.execute(select(InstallationMetadata))
    marker = result.scalar_one()
    assert marker.mode == InstallationMode.LOCAL_DEMO
    assert marker.id == 1


@pytest.mark.asyncio
async def test_initialize_demo_marker_is_idempotent(session: AsyncSession):
    """Test that initialize_demo_marker is idempotent"""
    # Initialize marker twice
    await initialize_demo_marker(session)
    await initialize_demo_marker(session)
    
    # Verify only one marker exists
    result = await session.execute(select(func.count()).select_from(InstallationMetadata))
    count = result.scalar_one()
    assert count == 1
    
    # Verify it's still local_demo
    result = await session.execute(select(InstallationMetadata))
    marker = result.scalar_one()
    assert marker.mode == InstallationMode.LOCAL_DEMO


@pytest.mark.asyncio
async def test_initialize_demo_marker_refuses_authenticated_database(session: AsyncSession):
    """Test that initialize_demo_marker refuses authenticated database"""
    # Create authenticated marker
    marker = InstallationMetadata(id=1, mode=InstallationMode.AUTHENTICATED)
    session.add(marker)
    await session.commit()
    
    # Attempt to initialize demo should fail
    with pytest.raises(RuntimeError, match="Cannot initialize demo on an authenticated database"):
        await initialize_demo_marker(session)
    
    # Verify marker is still authenticated
    result = await session.execute(select(InstallationMetadata))
    marker = result.scalar_one()
    assert marker.mode == InstallationMode.AUTHENTICATED


@pytest.mark.asyncio
async def test_create_demo_user_creates_fixed_user(session: AsyncSession):
    """Test that create_demo_user creates user with fixed UUID and identity"""
    demo_user = await create_demo_user(session)
    
    assert demo_user.id == DEMO_USER_UUID
    assert demo_user.issuer == DEMO_ISSUER
    assert demo_user.subject == DEMO_SUBJECT
    assert demo_user.email == "demo@farmtwin.local"
    assert demo_user.display_name == "Demo User"
    assert demo_user.preferences == {}


@pytest.mark.asyncio
async def test_create_demo_user_is_idempotent(session: AsyncSession):
    """Test that create_demo_user is idempotent"""
    # Create demo user twice
    user1 = await create_demo_user(session)
    user2 = await create_demo_user(session)
    
    # Should be the same user
    assert user1.id == user2.id == DEMO_USER_UUID
    
    # Verify only one user exists
    result = await session.execute(select(func.count()).select_from(User))
    count = result.scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_seed_demo_farms_creates_fixtures(session: AsyncSession):
    """Test that seed_demo_farms creates farm fixtures with historical timestamps"""
    # Create demo user first
    demo_user = await create_demo_user(session)
    
    # Seed farms
    await seed_demo_farms(session, demo_user)
    
    # Verify farms were created
    result = await session.execute(
        select(Farm).where(Farm.user_id == demo_user.id)
    )
    farms = result.scalars().all()
    
    assert len(farms) > 0
    for farm in farms:
        # Names should be marked as demo
        assert "[DEMO]" in farm.name
        
        # Timestamps should be historical (June 2025)
        assert farm.created_at.year == 2025
        assert farm.created_at.month == 6
        
        # Should have geometry revisions
        assert farm.current_geometry_revision == 1


@pytest.mark.asyncio
async def test_seed_demo_farms_preserves_historical_timestamps(session: AsyncSession):
    """Test that seed_demo_farms preserves historical fixture timestamps"""
    demo_user = await create_demo_user(session)
    await seed_demo_farms(session, demo_user)
    
    # Get farms and revisions
    result = await session.execute(
        select(Farm).where(Farm.user_id == demo_user.id)
    )
    farms = result.scalars().all()
    
    for farm in farms:
        # Farm timestamp should be historical
        historical_time = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        assert farm.created_at == historical_time
        assert farm.updated_at == historical_time
        
        # Get geometry revisions
        result = await session.execute(
            select(FarmGeometryRevision).where(FarmGeometryRevision.farm_id == farm.id)
        )
        revisions = result.scalars().all()
        
        for revision in revisions:
            # Revision timestamp should also be historical
            assert revision.created_at == historical_time


@pytest.mark.asyncio
async def test_seed_demo_farms_is_idempotent(session: AsyncSession):
    """Test that seed_demo_farms is idempotent"""
    demo_user = await create_demo_user(session)
    
    # Seed farms twice
    await seed_demo_farms(session, demo_user)
    await seed_demo_farms(session, demo_user)
    
    # Count farms - should not duplicate
    result = await session.execute(
        select(func.count()).select_from(Farm).where(Farm.user_id == demo_user.id)
    )
    count = result.scalar_one()
    
    # Should have the original fixtures, not duplicates
    # (exact count depends on fixture definition, but should be consistent)
    assert count == 2  # Based on current fixture definition


@pytest.mark.asyncio
async def test_seed_demo_farms_has_valid_geometry(session: AsyncSession):
    """Test that seeded farms have valid WGS84 geometry"""
    demo_user = await create_demo_user(session)
    await seed_demo_farms(session, demo_user)
    
    # Get geometry revisions
    result = await session.execute(select(FarmGeometryRevision))
    revisions = result.scalars().all()
    
    for revision in revisions:
        # Should have non-null geometry fields
        assert revision.geometry is not None
        assert revision.centroid is not None
        assert revision.label_point is not None
        
        # Hectares should be positive and finite
        assert revision.hectares > 0
        assert revision.hectares == revision.hectares  # NaN check


def test_setup_demo_database_refuses_wrong_auth_mode():
    """Test that setup validates auth mode before attempting database connection"""
    # Use OIDC mode which is incompatible with demo
    settings = Settings(
        _env_file=None,
        environment="development",
        data_mode="demonstration",
        auth={
            "mode": "oidc",
            "issuer": "https://example.com",
            "audience": "farmtwin",
            "jwks_url": "https://example.com/.well-known/jwks.json",
            "algorithms": ["RS256"],
        },
        demo={"local_only": True, "isolated_database": True},
        database={
            "host": "localhost",
            "name": "farmtwin_demo",
            "user": "demo_user",
            "password": "demo_pass",
        },
    )
    
    # Should fail validation before attempting database connection
    import asyncio
    with pytest.raises(RuntimeError, match="Demo setup requires AUTH__MODE=local_demo"):
        asyncio.run(setup_demo_database(settings, seed_farms=False))


def test_setup_demo_database_refuses_wrong_environment():
    """Test that config validation rejects non-development environment for demo"""
    # Settings validation should reject this at config level
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="LOCAL_DEMO requires ENVIRONMENT=development"):
        Settings(
            _env_file=None,
            environment="production",  # Wrong environment
            data_mode="demonstration",
            auth={"mode": "local_demo"},
            demo={"local_only": True, "isolated_database": True},
            database={"host": "localhost", "name": "farmtwin_demo", "user": "demo_user", "password": "demo_pass"},
        )


def test_local_authentication_allows_live_environmental_data():
    """Local development auth does not force the use of fixture data."""
    settings = Settings(
        _env_file=None,
        environment="development",
        data_mode="live",
        auth={"mode": "local_demo"},
        demo={"local_only": True, "isolated_database": True},
        database={"host": "localhost", "name": "farmtwin_demo", "user": "demo_user", "password": "demo_pass"},
    )
    assert settings.data_mode == DataMode.LIVE


def test_setup_demo_database_requires_local_only():
    """Test that config validation requires DEMO__LOCAL_ONLY=true for demo"""
    # Settings validation should catch this when auth mode is local_demo
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="LOCAL_DEMO requires DEMO__LOCAL_ONLY=true"):
        Settings(
            _env_file=None,
            environment="development",
            data_mode="demonstration",
            auth={"mode": "local_demo"},
            demo={"local_only": False, "isolated_database": True},
            database={"host": "localhost", "name": "farmtwin_demo", "user": "demo_user", "password": "demo_pass"},
        )


def test_setup_demo_database_requires_isolated_database():
    """Test that config validation requires DEMO__ISOLATED_DATABASE=true for demo"""
    # Settings validation should catch this when auth mode is local_demo
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="LOCAL_DEMO requires DEMO__ISOLATED_DATABASE=true"):
        Settings(
            _env_file=None,
            environment="development",
            data_mode="demonstration",
            auth={"mode": "local_demo"},
            demo={"local_only": True, "isolated_database": False},
            database={"host": "localhost", "name": "farmtwin_demo", "user": "demo_user", "password": "demo_pass"},
        )


@pytest.mark.asyncio
async def test_demo_fixtures_do_not_mix_with_real_users(session: AsyncSession):
    """Test that demo fixtures are isolated from real users"""
    # Create demo user and farms
    demo_user = await create_demo_user(session)
    await seed_demo_farms(session, demo_user)
    
    # Create a "real" user (simulated)
    real_user = User(
        issuer="https://real-provider.com",
        subject="real-user-123",
        email="real@example.com",
        display_name="Real User",
    )
    session.add(real_user)
    await session.commit()
    
    # Verify demo farms belong only to demo user
    result = await session.execute(
        select(Farm).where(Farm.user_id == demo_user.id)
    )
    demo_farms = result.scalars().all()
    assert len(demo_farms) > 0
    assert all(farm.user_id == demo_user.id for farm in demo_farms)
    
    # Verify real user has no farms
    result = await session.execute(
        select(Farm).where(Farm.user_id == real_user.id)
    )
    real_farms = result.scalars().all()
    assert len(real_farms) == 0
    
    # Verify demo user UUID is distinct
    assert demo_user.id != real_user.id


@pytest.mark.asyncio
async def test_complete_setup_is_idempotent(session: AsyncSession):
    """Test that complete demo setup can be run multiple times safely"""
    # Note: This test uses the session fixture which connects to a test database
    # We'll test individual components since setup_demo_database creates its own engine
    
    # Run setup steps twice
    await initialize_demo_marker(session)
    demo_user = await create_demo_user(session)
    await seed_demo_farms(session, demo_user)
    
    # Run again
    await initialize_demo_marker(session)
    demo_user2 = await create_demo_user(session)
    await seed_demo_farms(session, demo_user2)
    
    # Verify single marker
    result = await session.execute(select(func.count()).select_from(InstallationMetadata))
    marker_count = result.scalar_one()
    assert marker_count == 1
    
    # Verify single user
    result = await session.execute(select(func.count()).select_from(User))
    user_count = result.scalar_one()
    assert user_count == 1
    
    # Verify farms not duplicated
    result = await session.execute(
        select(func.count()).select_from(Farm).where(Farm.user_id == demo_user.id)
    )
    farm_count = result.scalar_one()
    assert farm_count == 2  # Original fixtures, not doubled


@pytest.mark.asyncio
async def test_demo_responses_remain_non_live_after_restart():
    """
    Test that demo responses and logs remain labeled non-live after restart.
    
    Requirements: 1.6, 4.3, 12.2, 14.3
    
    **Property: Demo isolation**
    Feature: backend-foundation, Property 10: Honest data modes
    
    This test verifies:
    - Demo responses include non_live header after restart
    - Demo data mode headers remain consistent
    - Historical fixture timestamps are preserved after restart
    """
    import os
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from httpx import AsyncClient, ASGITransport
    
    from app.main import create_app
    from app.core.config import Settings, Environment, DataMode
    
    # Get test database URL
    database_url = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://farmtwin_test:test_password@localhost:5434/farmtwin_test"
    )
    
    # Create demo settings using the same pattern as the fixture
    demo_settings = Settings(
        environment=Environment.DEVELOPMENT,
        data_mode=DataMode.DEMONSTRATION,
        auth={"mode": "local_demo"},
        database={"host": "localhost", "port": 5434, "name": "farmtwin_test", "user": "farmtwin_test", "password": "test_password"},
        demo={"local_only": True, "isolated_database": True},
        cors={"origins": []},
    )
    
    # Create engine and run demo setup
    engine = create_async_engine(database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    
    try:
        # Setup demo database
        async with session_factory() as session:
            await initialize_demo_marker(session)
            demo_user = await create_demo_user(session)
            await seed_demo_farms(session, demo_user)
        
        # Create first app instance
        app = create_app(demo_settings)
        app.state.session_factory = session_factory
        
        # Make request to get farms
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response1 = await client.get("/api/v1/farms")
        
        assert response1.status_code == 200
        farms1 = response1.json()
        
        # Verify non-live headers in first instance
        assert response1.headers["X-FarmTwin-Data-Mode"] == "demonstration"
        assert response1.headers["X-FarmTwin-Auth-Mode"] == "local_demo"
        
        # Verify farms have historical timestamps
        assert len(farms1["items"]) > 0
        for farm in farms1["items"]:
            # Parse timestamp and verify it's the historical June 2025 timestamp
            created_at = datetime.fromisoformat(farm["created_at"].replace("Z", "+00:00"))
            assert created_at.year == 2025
            assert created_at.month == 6
        
        # Verify health endpoint shows non_live
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            health1 = await client.get("/health")
        assert health1.status_code == 200
        assert health1.json()["non_live"] is True
        
        # Dispose engine (simulate restart)
        await engine.dispose()
        
        # Create new engine and app instance (simulating restart)
        restarted_engine = create_async_engine(database_url, poolclass=NullPool)
        restarted_session_factory = async_sessionmaker(restarted_engine, expire_on_commit=False)
        restarted_app = create_app(demo_settings)
        restarted_app.state.session_factory = restarted_session_factory
        
        try:
            # Make request to restarted app
            async with AsyncClient(transport=ASGITransport(app=restarted_app), base_url="http://test") as client:
                response2 = await client.get("/api/v1/farms")
            
            assert response2.status_code == 200
            farms2 = response2.json()
            
            # Verify non-live headers still present after restart
            assert response2.headers["X-FarmTwin-Data-Mode"] == "demonstration"
            assert response2.headers["X-FarmTwin-Auth-Mode"] == "local_demo"
            
            # Verify historical timestamps are preserved after restart
            assert len(farms2["items"]) > 0
            for farm in farms2["items"]:
                created_at = datetime.fromisoformat(farm["created_at"].replace("Z", "+00:00"))
                assert created_at.year == 2025
                assert created_at.month == 6
            
            # Verify same farms are returned
            assert farms2["total"] == farms1["total"]
            assert len(farms2["items"]) == len(farms1["items"])
            
            # Verify health endpoint still shows non_live after restart
            async with AsyncClient(transport=ASGITransport(app=restarted_app), base_url="http://test") as client:
                health2 = await client.get("/health")
            assert health2.status_code == 200
            assert health2.json()["non_live"] is True
            
        finally:
            await restarted_engine.dispose()
    
    finally:
        await engine.dispose()
