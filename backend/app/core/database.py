"""
Database engine and session management.

Requirements: 2.6, 2.8, 6.8, 9.5, 10.7, 10.8
"""

import asyncio
from typing import AsyncGenerator, Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import text

from .config import Settings


EXPECTED_ALEMBIC_HEAD = "0010_farm_delete_permissions"
READINESS_OPERATION_SECONDS = 3.0
READINESS_CLEANUP_SECONDS = 1.0


def create_engine(settings: Settings) -> AsyncEngine:
    """
    Create async database engine with configured timeouts and pool settings.
    
    Requirements: 2.6, 2.8, 9.5, 10.7
    
    Configuration:
    - Pool size and overflow from settings
    - Pool timeout within readiness budget
    - Connect timeout within readiness budget  
    - Statement timeout via connect args
    - SQL echo disabled for security
    - No parameter logging to avoid credential leaks
    """
    db_url = settings.database.get_url()
    
    return create_async_engine(
        db_url,
        # Pool configuration - within readiness budget
        pool_size=settings.database.pool_size,
        max_overflow=settings.database.max_overflow,
        pool_timeout=settings.database.pool_timeout_seconds,
        # Connection timeout
        connect_args={
            "timeout": settings.database.connect_timeout_seconds,
            "command_timeout": settings.database.connect_timeout_seconds,
            "server_settings": {
                # Statement timeout in milliseconds for PostgreSQL
                "statement_timeout": str(settings.database.statement_timeout_ms),
            },
        },
        # Security: disable SQL echo and parameter logging
        echo=False,
        echo_pool=False,
        # Hide parameters to avoid credential leaks in logs
        hide_parameters=True,
        # Future-proof: SQLAlchemy 2.0 behavior
        future=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """
    Create async session factory bound to the engine.
    
    Requirements: 10.7
    
    Sessions created by this factory:
    - Are async-native for I/O operations
    - Must not be shared across concurrent tasks
    - Must be explicitly closed after use
    - Do not auto-commit (transaction ownership in services)
    """
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        # Disable autocommit - services own transaction boundaries
        autocommit=False,
        autoflush=False,
        # Sessions must be explicitly closed
        expire_on_commit=False,
    )


async def get_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency that provides a database session for a request.
    
    Requirements: 6.8, 10.7, 10.8
    
    Lifecycle:
    - Creates a new session per request/task
    - Never shares sessions across concurrent operations
    - Always closes the session (success or failure)
    - Does NOT commit after response (services own commits)
    - Rolls back on exception/cancellation
    - Releases or invalidates connection on completion
    
    Transaction ownership:
    - Services explicitly commit after materializing response data
    - This dependency never commits, even on successful requests
    - Uncommitted changes are rolled back in finally block
    - Connection is always released/invalidated via close()
    """
    session = session_factory()
    try:
        yield session
    except BaseException:
        # Roll back on any exception (failure or cancellation)
        # Using BaseException to catch CancelledError (inherits from BaseException, not Exception)
        await session.rollback()
        raise
    finally:
        # Always close the session to release/invalidate connection
        # If a service committed, this just closes the session
        # If no commit occurred, this rolls back and closes
        await session.close()


# Global session factory instance
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """
    Get the global session factory for FastAPI dependency injection.
    
    Requirements: 10.7
    
    This function returns the session factory initialized during application
    startup. It's used by dependencies that need to create their own sessions
    (e.g., authentication that needs a separate transaction).
    
    Raises:
        RuntimeError: If called before application startup initialized the factory
    """
    if _session_factory is None:
        raise RuntimeError(
            "Session factory not initialized. "
            "Application startup must call set_session_factory()."
        )
    return _session_factory


def set_session_factory(factory: async_sessionmaker[AsyncSession]) -> None:
    """
    Set the global session factory (called during application startup).
    
    Requirements: 10.7
    """
    global _session_factory
    _session_factory = factory


def clear_session_factory() -> None:
    """Clear the session factory (primarily for testing)"""
    global _session_factory
    _session_factory = None



async def check_readiness(
    session_factory: async_sessionmaker[AsyncSession],
    settings: "Settings",
) -> tuple[dict[str, str], bool]:
    """
    Check database readiness with bounded timeout.
    
    Requirements: 3.2, 3.3, 3.4, 3.5, 3.6
    
    Checks:
    - Database connectivity (SELECT 1)
    - PostGIS availability
    - Alembic migration head matches expected
    - Installation marker matches auth mode
    
    Returns:
        Tuple of (checks dict, is_ready bool)
        checks: {"database": "ok"/"unavailable", "postgis": "ok"/"unavailable", ...}
        is_ready: True if all checks pass, False otherwise
    
    Completes within 5 seconds total including acquisition, checks and cleanup.
    Individual operations have bounded timeouts that sum to less than 5 seconds.
    """
    checks: dict[str, str] = {}
    session: Any | None = None
    timed_out = False
    stage = "database"

    try:
        async with asyncio.timeout(READINESS_OPERATION_SECONDS):
            # Creating an AsyncSession does not acquire a pool connection. The
            # first execute does, so the same deadline includes acquisition.
            session = session_factory()

            await session.execute(text("SELECT 1"))
            checks["database"] = "ok"

            stage = "postgis"
            result = await session.execute(text("SELECT PostGIS_Version()"))
            checks["postgis"] = "ok" if result.scalar_one_or_none() else "missing"

            stage = "migrations"
            result = await session.execute(
                text("SELECT version_num FROM alembic_version")
            )
            revision = result.scalar_one_or_none()
            checks["migrations"] = (
                "ok" if revision == EXPECTED_ALEMBIC_HEAD else "incompatible"
            )

            stage = "installation"
            result = await session.execute(
                text("SELECT mode FROM installation_metadata WHERE id = 1")
            )
            marker = result.scalar_one_or_none()
            expected_marker = (
                "local_demo" if settings.auth.mode.value == "local_demo" else "authenticated"
            )
            normalized_marker = str(getattr(marker, "value", marker) or "").lower()
            checks["installation"] = (
                "ok" if normalized_marker == expected_marker else "marker_mismatch"
            )
    except TimeoutError:
        timed_out = True
        checks[stage] = "timeout"
    except Exception:
        checks[stage] = "unavailable"
    finally:
        if session is not None:
            try:
                async with asyncio.timeout(READINESS_CLEANUP_SECONDS):
                    if timed_out and hasattr(session, "invalidate"):
                        await session.invalidate()
                    else:
                        await session.rollback()
                    await session.close()
            except (Exception, TimeoutError):
                checks["cleanup"] = "incomplete"

    required = {"database", "postgis", "migrations", "installation"}
    is_ready = (
        required <= checks.keys()
        and all(checks[name] == "ok" for name in required)
        and "cleanup" not in checks
    )
    return checks, is_ready
