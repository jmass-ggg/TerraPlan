"""
Alembic migration environment configuration.

Requirements: 2.1, 2.2

This module configures Alembic to:
1. Import model metadata explicitly (not via auto-import)
2. Use transactional version-marker updates for transactional DDL
3. Run through management commands only (never during requests)
4. Support both offline and online migration modes
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import model metadata explicitly
# This ensures all models are registered before generating migrations
from app.models import Base

# Import models explicitly to ensure they're registered with metadata
# Even though they're in __all__, we import them to be explicit
from app.models.user import User, InstallationMetadata
from app.models.farm import Farm, FarmGeometryRevision

# Alembic Config object
config = context.config

# Interpret the config file for Python logging unless we're in quiet mode
if config.config_file_name is not None:
    # Migration setup may run in the same process as lifecycle verification.
    # Preserve the application's privacy-filtered loggers rather than silently
    # disabling request diagnostics through fileConfig's legacy default.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Model metadata for 'autogenerate' support
# Using Base.metadata which includes all registered models
target_metadata = Base.metadata


def get_url_from_env() -> str:
    """
    Get database URL from environment.
    
    This is called when migrations are run via management command,
    which will set the DATABASE_URL environment variable from Settings.
    
    Do not allow migrations to run without explicit environment configuration.
    """
    import os
    
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError(
            "DATABASE_URL environment variable is required for migrations. "
            "Migrations must be run through management commands, not directly."
        )
    return db_url


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine,
    though an Engine is acceptable here as well. By skipping the Engine
    creation we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    
    Requirements: 2.1
    """
    url = get_url_from_env()
    
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Transactional DDL for offline mode
        transaction_per_migration=True,
        # Compare types to detect changes
        compare_type=True,
        # Compare server defaults
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """
    Run migrations with the given connection.
    
    Uses transactional DDL to ensure version markers are updated
    atomically with schema changes.
    
    Requirements: 2.2
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Transactional DDL - version marker updates are transactional
        # If a migration fails, the version marker rollback with it
        transaction_per_migration=True,
        # Compare types to detect changes in column types
        compare_type=True,
        # Compare server defaults to detect changes
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """
    Run migrations in 'online' mode with async engine.
    
    In this scenario we need to create an async Engine and associate
    a connection with the context.
    
    Requirements: 2.1, 2.2
    """
    # Get configuration from alembic.ini
    configuration = config.get_section(config.config_ini_section, {})
    
    # Override database URL from environment
    configuration["sqlalchemy.url"] = get_url_from_env()
    
    # Create async engine
    # Use NullPool to avoid keeping connections open between migrations
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    async with connectable.connect() as connection:
        # Run migrations synchronously within the async connection
        await connection.run_sync(do_run_migrations)

    # Dispose engine after migrations complete
    await connectable.dispose()


def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode.
    
    For async engines (asyncpg), this creates an event loop and runs
    async migrations.
    
    Requirements: 2.1, 2.2
    """
    # Run async migrations
    asyncio.run(run_async_migrations())


# Determine which mode to run in
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
