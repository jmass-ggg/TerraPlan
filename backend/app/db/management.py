"""
Database management commands for migrations and setup.

Requirements: 2.1, 2.2, 12.1–12.6

This module provides CLI commands for database operations:
- Running Alembic migrations
- Generating new migrations
- Checking migration status
- Ingesting the Conduit historical fixture (ingest-fixture)

Migrations and ingestion MUST NOT run during requests or automatically at
startup. They are controlled operations run by administrators through these
commands.
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig

from app.core.config import DataMode, Settings


def get_alembic_config(settings: Settings) -> AlembicConfig:
    """
    Create Alembic configuration from application settings.
    
    Requirements: 2.1
    
    Sets the database URL from settings and configures paths relative
    to the backend directory.
    """
    # Get backend directory (parent of app directory)
    backend_dir = Path(__file__).parent.parent.parent
    alembic_ini_path = backend_dir / "alembic.ini"
    
    if not alembic_ini_path.exists():
        raise RuntimeError(
            f"Alembic configuration not found at {alembic_ini_path}. "
            "Ensure alembic.ini exists in the backend directory."
        )
    
    # Create Alembic config
    alembic_cfg = AlembicConfig(str(alembic_ini_path))
    
    # Set script location relative to backend directory
    script_location = backend_dir / "app" / "db" / "migrations"
    alembic_cfg.set_main_option("script_location", str(script_location))
    
    # Set database URL in environment for env.py to pick up
    # env.py will read this via get_url_from_env()
    db_url = settings.database.get_url()
    # Convert URL object to string for environment variable
    # MUST use render_as_string(hide_password=False) to include actual password
    os.environ["DATABASE_URL"] = db_url.render_as_string(hide_password=False)
    
    return alembic_cfg


def migrate_upgrade(settings: Settings, revision: str = "head") -> None:
    """
    Upgrade database to a specific revision.
    
    Requirements: 2.1, 2.2
    
    Args:
        settings: Application settings with database configuration
        revision: Target revision (default: "head" for latest)
    
    This uses transactional DDL where supported by the database.
    Version markers are updated atomically with schema changes.
    If a migration fails, the version marker rolls back with it.
    """
    alembic_cfg = get_alembic_config(settings)
    
    print(f"Upgrading database to revision: {revision}")
    command.upgrade(alembic_cfg, revision)
    print("Migration upgrade complete")


def migrate_downgrade(settings: Settings, revision: str) -> None:
    """
    Downgrade database to a specific revision.
    
    Requirements: 2.1, 2.2
    
    Args:
        settings: Application settings with database configuration
        revision: Target revision (e.g., "-1" for previous, or specific revision)
    
    WARNING: Downgrade should only be used in development/testing.
    Production downgrades require careful review and may not be safe
    for all migration types (especially data migrations).
    """
    alembic_cfg = get_alembic_config(settings)
    
    print(f"Downgrading database to revision: {revision}")
    command.downgrade(alembic_cfg, revision)
    print("Migration downgrade complete")


def migrate_current(settings: Settings) -> None:
    """
    Show current database revision.
    
    Requirements: 2.1
    
    Args:
        settings: Application settings with database configuration
    """
    alembic_cfg = get_alembic_config(settings)
    
    print("Current database revision:")
    command.current(alembic_cfg, verbose=True)


def migrate_history(settings: Settings) -> None:
    """
    Show migration history.
    
    Requirements: 2.1
    
    Args:
        settings: Application settings with database configuration
    """
    alembic_cfg = get_alembic_config(settings)
    
    print("Migration history:")
    command.history(alembic_cfg, verbose=True)


def migrate_heads(settings: Settings) -> None:
    """
    Show head revisions.
    
    Requirements: 2.1
    
    Args:
        settings: Application settings with database configuration
    """
    alembic_cfg = get_alembic_config(settings)
    
    print("Head revisions:")
    command.heads(alembic_cfg, verbose=True)


def migrate_revision(
    settings: Settings,
    message: str,
    autogenerate: bool = False,
) -> None:
    """
    Create a new migration revision.
    
    Requirements: 2.1
    
    Args:
        settings: Application settings with database configuration
        message: Description of the migration
        autogenerate: Whether to auto-detect schema changes
    
    When autogenerate=True, Alembic compares the current database schema
    to the model metadata and generates a migration script with the differences.
    
    Always review auto-generated migrations before applying them.
    """
    alembic_cfg = get_alembic_config(settings)
    
    print(f"Creating new migration: {message}")
    if autogenerate:
        print("Auto-generating migration based on model changes...")
    
    command.revision(
        alembic_cfg,
        message=message,
        autogenerate=autogenerate,
    )
    
    print("Migration revision created. Review the generated file before applying.")


def ingest_fixture_command(settings: Settings, fixture_path: Path) -> None:
    """
    Run the Conduit fixture ingestion pipeline.

    Requirements: 12.1–12.6

    Args:
        settings: Application settings. Must have data_mode != live.
        fixture_path: Path to the JSON fixture file.

    Prints a structured JSON summary to stdout on success.
    Exits nonzero on any error.
    """
    # Requirement 12.3 — refuse when DATA_MODE=live
    if settings.data_mode == DataMode.LIVE:
        error_summary = {
            "status": "error",
            "error": "CONFIG_ERROR",
            "message": (
                "ingest-fixture refused: DATA_MODE is 'live'. "
                "Run with DATA_MODE=historical_replay or DATA_MODE=demonstration."
            ),
        }
        print(json.dumps(error_summary, indent=2), file=sys.stderr)
        sys.exit(1)

    # Requirement 12.5 — fixture file must exist
    if not fixture_path.exists():
        error_summary = {
            "status": "error",
            "error": "FILE_NOT_FOUND",
            "message": f"Fixture file not found: {fixture_path}",
        }
        print(json.dumps(error_summary, indent=2), file=sys.stderr)
        sys.exit(1)

    async def _run() -> None:
        from app.core.database import create_engine, create_session_factory
        from app.services.ingestion import (
            IngestionConfigError,
            IngestionError,
            ingest_fixture,
        )

        engine = create_engine(settings)
        session_factory = create_session_factory(engine)

        start = time.monotonic()
        try:
            run = await ingest_fixture(session_factory, settings, fixture_path)
        finally:
            await engine.dispose()

        elapsed = time.monotonic() - start

        summary = {
            "status": run.status.value,
            "run_id": str(run.id),
            "source_id": run.source_id,
            "accepted_count": run.accepted_count,
            "rejected_count": run.rejected_count,
            "duplicate_count": run.duplicate_count,
            "payload_size_bytes": run.payload_size_bytes,
            "sha256_checksum": run.sha256_checksum,
            "elapsed_seconds": round(elapsed, 3),
        }
        print(json.dumps(summary, indent=2))

    try:
        asyncio.run(_run())
    except (
        Exception
    ) as exc:
        error_summary = {
            "status": "error",
            "error": type(exc).__name__,
            "message": str(exc),
        }
        print(json.dumps(error_summary, indent=2), file=sys.stderr)
        sys.exit(1)


def main() -> None:
    """
    CLI entry point for database management commands.

    Requirements: 2.1, 2.2, 12.1–12.6

    Usage:
        python -m app.db.management upgrade
        python -m app.db.management current
        python -m app.db.management history
        python -m app.db.management revision "Add new table" --autogenerate
        python -m app.db.management ingest-fixture [--fixture-path PATH]
    """
    # Load settings from environment
    settings = Settings()

    # Parse command line arguments
    if len(sys.argv) < 2:
        print("Usage: python -m app.db.management <command> [options]")
        print()
        print("Commands:")
        print("  upgrade [revision]           - Upgrade to revision (default: head)")
        print("  downgrade <revision>         - Downgrade to revision")
        print("  current                      - Show current revision")
        print("  history                      - Show migration history")
        print("  heads                        - Show head revisions")
        print("  revision <message>           - Create new migration")
        print("                                 Use --autogenerate to auto-detect changes")
        print("  ingest-fixture               - Ingest Conduit historical fixture")
        print("    [--fixture-path PATH]        Default: data/samples/weather.json")
        sys.exit(1)

    command_name = sys.argv[1]

    try:
        if command_name == "upgrade":
            revision = sys.argv[2] if len(sys.argv) > 2 else "head"
            migrate_upgrade(settings, revision)

        elif command_name == "downgrade":
            if len(sys.argv) < 3:
                print("Error: downgrade requires a revision argument")
                sys.exit(1)
            revision = sys.argv[2]
            migrate_downgrade(settings, revision)

        elif command_name == "current":
            migrate_current(settings)

        elif command_name == "history":
            migrate_history(settings)

        elif command_name == "heads":
            migrate_heads(settings)

        elif command_name == "revision":
            if len(sys.argv) < 3:
                print("Error: revision requires a message argument")
                sys.exit(1)
            message = sys.argv[2]
            autogenerate = "--autogenerate" in sys.argv
            migrate_revision(settings, message, autogenerate)

        elif command_name == "ingest-fixture":
            # Resolve --fixture-path if provided; otherwise use default path.
            # Default is relative to the repo root (two levels above backend/).
            backend_dir = Path(__file__).parent.parent.parent
            repo_root = backend_dir.parent.parent
            default_fixture = repo_root / "data" / "samples" / "weather.json"

            fixture_path = default_fixture
            args = sys.argv[2:]
            if "--fixture-path" in args:
                idx = args.index("--fixture-path")
                if idx + 1 >= len(args):
                    print("Error: --fixture-path requires a PATH argument", file=sys.stderr)
                    sys.exit(1)
                fixture_path = Path(args[idx + 1])

            ingest_fixture_command(settings, fixture_path)

        else:
            print(f"Error: Unknown command '{command_name}'")
            sys.exit(1)

    except SystemExit:
        raise
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
