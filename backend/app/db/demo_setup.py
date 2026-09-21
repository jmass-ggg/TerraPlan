"""
Local demo setup command for explicit demo database initialization.

Requirements: 1.6, 4.3, 12.2

This module provides an explicit idempotent command to initialize the local-demo
installation marker, fixed demo principal, and labeled historical/synthetic
fixture records in an isolated demo database.

The command:
- Initializes the local_demo installation marker
- Creates the fixed demo user with reserved identity
- Seeds labeled historical/synthetic farm fixtures (optional)
- Is idempotent (can be run multiple times safely)
- Refuses authenticated or non-local databases
- Preserves fixture timestamps
- Avoids production migration seeds

Demo fixtures use explicit historical timestamps (not current time) to indicate
they are not live data. The demo mode header is set at the API level based on
configuration, not from record metadata.
"""

import sys
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from geoalchemy2.shape import from_shape
from shapely.geometry import Point, Polygon
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import AuthMode, Environment, Settings
from app.core.database import create_engine
from app.core.security import DEMO_ISSUER, DEMO_SUBJECT, DEMO_USER_UUID
from app.models.farm import Farm, FarmGeometryRevision
from app.models.user import InstallationMetadata, InstallationMode, User


async def check_installation_marker(session: AsyncSession) -> InstallationMode | None:
    """
    Check if an installation marker already exists.
    
    Returns:
        The current installation mode, or None if no marker exists
    """
    result = await session.execute(select(InstallationMetadata))
    metadata = result.scalar_one_or_none()
    return metadata.mode if metadata else None


async def initialize_demo_marker(session: AsyncSession) -> None:
    """
    Initialize the local_demo installation marker.
    
    Requirements: 1.6, 4.3, 12.2
    
    This is idempotent - if the marker already exists and is local_demo,
    this is a no-op. If it exists but is authenticated, this raises an error.
    """
    existing_mode = await check_installation_marker(session)
    
    if existing_mode == InstallationMode.LOCAL_DEMO:
        print("Installation marker already set to local_demo")
        return
    
    if existing_mode == InstallationMode.AUTHENTICATED:
        raise RuntimeError(
            "Cannot initialize demo on an authenticated database. "
            "This database is marked as 'authenticated' and cannot be converted "
            "to demo mode. Use a separate isolated database for demo."
        )
    
    # Create the marker
    marker = InstallationMetadata(
        id=1,
        mode=InstallationMode.LOCAL_DEMO,
    )
    session.add(marker)
    await session.commit()
    print("✓ Initialized local_demo installation marker")


async def create_demo_user(session: AsyncSession) -> User:
    """
    Create or retrieve the fixed demo user.
    
    Requirements: 1.6, 4.3, 12.2
    
    Creates a user with:
    - Fixed UUID (DEMO_USER_UUID)
    - Reserved local issuer/subject
    - Optional profile fields
    - Empty preferences
    
    This is idempotent - if the user already exists, it returns the existing user.
    """
    # Check if demo user already exists by UUID
    result = await session.execute(
        select(User).where(User.id == DEMO_USER_UUID)
    )
    existing_user = result.scalar_one_or_none()
    
    if existing_user:
        print(f"Demo user already exists: {existing_user.id}")
        return existing_user
    
    # Check if a user with demo issuer/subject exists (shouldn't happen, but check)
    result = await session.execute(
        select(User).where(
            User.issuer == DEMO_ISSUER,
            User.subject == DEMO_SUBJECT,
        )
    )
    existing_by_identity = result.scalar_one_or_none()
    
    if existing_by_identity:
        print(
            f"Demo identity already exists with different UUID: "
            f"{existing_by_identity.id}"
        )
        return existing_by_identity
    
    # Create the demo user with fixed UUID
    demo_user = User(
        id=DEMO_USER_UUID,
        issuer=DEMO_ISSUER,
        subject=DEMO_SUBJECT,
        email="demo@farmtwin.local",
        display_name="Demo User",
        preferences={},
    )
    
    session.add(demo_user)
    
    try:
        await session.commit()
        print(f"✓ Created demo user: {demo_user.id}")
    except IntegrityError as e:
        await session.rollback()
        # If there was a race condition, fetch the existing user
        result = await session.execute(
            select(User).where(User.id == DEMO_USER_UUID)
        )
        demo_user = result.scalar_one()
        print(f"Demo user created by concurrent process: {demo_user.id}")
    
    return demo_user


async def seed_demo_farms(session: AsyncSession, demo_user: User) -> None:
    """
    Seed historical/synthetic farm fixtures for demo.
    
    Requirements: 1.6, 4.3, 12.2
    
    Creates labeled historical/synthetic fixture farms with:
    - Explicit historical timestamps (June 2025)
    - Simple valid polygons in WGS84
    - Computed area/centroid/label point
    
    These are clearly marked as fixtures through their names and timestamps.
    They preserve fixture timestamps and are NOT represented as current data.
    
    This is idempotent - if farms already exist for the demo user, this is a no-op.
    """
    # Check if demo user already has farms
    result = await session.execute(
        select(Farm).where(Farm.user_id == demo_user.id)
    )
    existing_farms = result.scalars().all()
    
    if existing_farms:
        print(f"Demo user already has {len(existing_farms)} farm(s), skipping seed")
        return
    
    # Historical timestamp for fixtures (June 2025 as per project documents)
    historical_timestamp = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    
    # Define demo farm fixtures with simple valid geometries
    fixtures = [
        {
            "name": "[DEMO] Green Valley Farm",
            "polygon": Polygon([
                (-122.4, 37.8),
                (-122.4, 37.9),
                (-122.3, 37.9),
                (-122.3, 37.8),
                (-122.4, 37.8),
            ]),
            "hectares": 1234.5,
        },
        {
            "name": "[DEMO] Sunrise Acres",
            "polygon": Polygon([
                (-121.5, 38.5),
                (-121.5, 38.6),
                (-121.4, 38.6),
                (-121.4, 38.5),
                (-121.5, 38.5),
            ]),
            "hectares": 567.8,
        },
    ]
    
    for idx, fixture_data in enumerate(fixtures, start=1):
        polygon = fixture_data["polygon"]
        centroid = polygon.centroid
        # Use centroid as label point for simplicity
        label_point = centroid
        
        # Convert Shapely geometries to WKB with SRID
        geometry_wkb = from_shape(polygon, srid=4326)
        centroid_wkb = from_shape(centroid, srid=4326)
        label_point_wkb = from_shape(label_point, srid=4326)
        
        # Create farm with historical timestamp
        farm = Farm(
            user_id=demo_user.id,
            name=fixture_data["name"],
            current_geometry_revision=1,
            created_at=historical_timestamp,
            updated_at=historical_timestamp,
        )
        session.add(farm)

        # The UUID is generated by PostgreSQL. Flush the farm first so the
        # revision receives a real foreign key; the deferred current-geometry
        # constraint permits both rows to be completed in this transaction.
        await session.flush()
        
        # Create initial geometry revision
        revision = FarmGeometryRevision(
            farm_id=farm.id,
            revision=1,
            geometry=geometry_wkb,
            centroid=centroid_wkb,
            label_point=label_point_wkb,
            hectares=fixture_data["hectares"],
            created_at=historical_timestamp,
        )
        
        session.add(revision)
    
    await session.commit()
    print(f"✓ Seeded {len(fixtures)} demo farm fixtures with historical timestamps")


async def setup_demo_database(
    settings: Settings,
    seed_farms: bool = True,
) -> None:
    """
    Execute the complete demo database setup.
    
    Requirements: 1.6, 4.3, 12.2
    
    Args:
        settings: Application settings with database configuration
        seed_farms: Whether to seed demo farm fixtures (default: True)
    
    This command:
    1. Validates configuration (demo mode requirements)
    2. Initializes the local_demo installation marker
    3. Creates the fixed demo user
    4. Seeds farm fixtures (if requested)
    
    The command is idempotent and can be run multiple times safely.
    """
    # Validate demo mode requirements
    if settings.auth.mode != AuthMode.LOCAL_DEMO:
        raise RuntimeError(
            f"Demo setup requires AUTH__MODE=local_demo, "
            f"got {settings.auth.mode.value}"
        )
    
    if settings.environment != Environment.DEVELOPMENT:
        raise RuntimeError(
            f"Demo setup requires ENVIRONMENT=development, "
            f"got {settings.environment.value}"
        )
    
    if not settings.demo.local_only:
        raise RuntimeError("Demo setup requires DEMO__LOCAL_ONLY=true")
    
    if not settings.demo.isolated_database:
        raise RuntimeError("Demo setup requires DEMO__ISOLATED_DATABASE=true")
    
    print("Demo setup configuration validated")
    print(f"Database: {settings.database.host}:{settings.database.port}/{settings.database.name}")
    print(f"Environment: {settings.environment.value}")
    print(f"Data mode: {settings.data_mode.value}")
    print()
    
    # Create engine and session factory
    engine = create_engine(settings)
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    
    try:
        # Initialize installation marker
        async with session_factory() as session:
            await initialize_demo_marker(session)
        
        # Create demo user
        async with session_factory() as session:
            demo_user = await create_demo_user(session)
        
        # Seed farm fixtures if requested
        if seed_farms:
            async with session_factory() as session:
                await seed_demo_farms(session, demo_user)
        else:
            print("Skipping farm fixtures (--no-farms)")
        
        print()
        print("✓ Demo database setup complete")
        print()
        print("The database is now initialized for local demo mode.")
        print("You can start the API with AUTH__MODE=local_demo.")
        
    finally:
        await engine.dispose()


def main() -> None:
    """
    CLI entry point for demo setup command.

    Requirements: 1.6, 4.3, 12.2

    Usage:
        python -m app.db.demo_setup              # full setup + seed farms
        python -m app.db.demo_setup --no-farms   # setup without seeding farms
        python -m app.db.demo_setup --cleanup    # remove seeded demo fixture farms
    """
    import asyncio

    cleanup = "--cleanup" in sys.argv
    seed_farms = "--no-farms" not in sys.argv and not cleanup
    
    # Load settings from environment
    try:
        settings = Settings()
    except Exception as e:
        print(f"Error loading settings: {e}", file=sys.stderr)
        print()
        print("Ensure environment variables are set correctly:", file=sys.stderr)
        print("  ENVIRONMENT=development", file=sys.stderr)
        print("  DATA_MODE=demonstration (or historical_replay)", file=sys.stderr)
        print("  AUTH__MODE=local_demo", file=sys.stderr)
        print("  DEMO__LOCAL_ONLY=true", file=sys.stderr)
        print("  DEMO__ISOLATED_DATABASE=true", file=sys.stderr)
        print("  DATABASE__HOST=...", file=sys.stderr)
        print("  DATABASE__NAME=... (isolated demo database)", file=sys.stderr)
        sys.exit(1)
    
    # Run async setup or cleanup
    try:
        if cleanup:
            from app.core.database import create_engine, create_session_factory
            engine = create_engine(settings)
            session_factory = create_session_factory(engine)

            async def _run_cleanup() -> None:
                try:
                    async with session_factory() as session:
                        await cleanup_demo_farms(session)
                finally:
                    await engine.dispose()

            asyncio.run(_run_cleanup())
        else:
            asyncio.run(setup_demo_database(settings, seed_farms=seed_farms))
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


async def cleanup_demo_farms(session: AsyncSession) -> int:
    """
    Remove seeded demo fixture farms from the demo database.

    Requirements: 1.6, 4.3

    Only removes farms that:
    - Belong to the fixed demo user UUID (00000000-0000-0000-0000-000000000001)
    - Have a name starting with '[DEMO]'

    Real user farms are never touched.  Returns the number of farms removed.
    """
    from sqlalchemy import text

    # Identify rows to delete (safety check before any write)
    result = await session.execute(
        select(Farm.id, Farm.name).where(
            Farm.user_id == DEMO_USER_UUID,
            Farm.name.like("[DEMO]%"),
        )
    )
    rows = result.all()
    if not rows:
        print("No demo fixture farms found — nothing to remove.")
        return 0

    ids = [r.id for r in rows]
    names = [r.name for r in rows]
    print(f"Removing {len(ids)} demo fixture farm(s): {names}")

    # Delete analysis snapshots and jobs (no cascade from farms to these tables)
    await session.execute(
        text(
            "DELETE FROM analysis_snapshots WHERE farm_id = ANY(:ids)"
        ),
        {"ids": ids},
    )
    await session.execute(
        text("DELETE FROM analysis_jobs WHERE farm_id = ANY(:ids)"),
        {"ids": ids},
    )

    # Delete farms — the FK from farm_geometry_revisions to farms has ON DELETE CASCADE
    # so revisions are removed automatically.  The circular FK fk_farms_current_geometry
    # is deferrable; defer it so we can delete the farm row before revisions are gone.
    await session.execute(text("SET CONSTRAINTS fk_farms_current_geometry DEFERRED"))
    await session.execute(
        text("DELETE FROM farms WHERE id = ANY(:ids)"),
        {"ids": ids},
    )
    await session.commit()
    print(f"✓ Removed {len(ids)} demo fixture farm(s)")
    return len(ids)


if __name__ == "__main__":
    main()
