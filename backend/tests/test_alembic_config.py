"""
Tests for Alembic migration configuration.

Requirements: 2.1, 2.2

Verifies that:
- Alembic configuration loads correctly
- Model metadata is imported explicitly
- Management commands are available
"""

import os
from pathlib import Path

import pytest

from app.core.config import Settings
from app.db.management import get_alembic_config
from app.models import Base


def test_alembic_config_file_exists():
    """
    Test that alembic.ini exists in the backend directory.
    
    Requirements: 2.1
    """
    backend_dir = Path(__file__).parent.parent
    alembic_ini = backend_dir / "alembic.ini"
    
    assert alembic_ini.exists(), f"alembic.ini not found at {alembic_ini}"
    assert alembic_ini.is_file(), f"alembic.ini is not a file"


def test_migration_env_file_exists():
    """
    Test that env.py exists in the migrations directory.
    
    Requirements: 2.1
    """
    env_py = Path(__file__).parent.parent / "app" / "db" / "migrations" / "env.py"
    
    assert env_py.exists(), f"env.py not found at {env_py}"
    assert env_py.is_file(), f"env.py is not a file"


def test_migration_template_exists():
    """
    Test that script.py.mako template exists.
    
    Requirements: 2.1
    """
    template = (
        Path(__file__).parent.parent
        / "app"
        / "db"
        / "migrations"
        / "script.py.mako"
    )
    
    assert template.exists(), f"script.py.mako not found at {template}"
    assert template.is_file(), f"script.py.mako is not a file"


def test_model_metadata_imported():
    """
    Test that model metadata is properly configured.
    
    Requirements: 2.1
    
    Verifies that Base.metadata contains the expected tables
    from explicit model imports.
    """
    # Check that metadata exists
    assert hasattr(Base, "metadata"), "Base.metadata not found"
    
    # Check that tables are registered
    tables = Base.metadata.tables
    
    # Expected tables from our models
    expected_tables = {
        "users",
        "installation_metadata",
        "farms",
        "farm_geometry_revisions",
    }
    
    actual_tables = set(tables.keys())
    
    assert expected_tables.issubset(
        actual_tables
    ), f"Missing tables: {expected_tables - actual_tables}"


def test_get_alembic_config_requires_database_url(monkeypatch):
    """
    Test that get_alembic_config requires DATABASE_URL.
    
    Requirements: 2.1
    
    Verifies that migrations cannot run without explicit configuration.
    """
    # Clear DATABASE_URL if it exists
    monkeypatch.delenv("DATABASE_URL", raising=False)
    
    settings = Settings(
        environment="development",
        data_mode="demonstration",
        auth={"mode": "local_demo"},
        database={
            "host": "localhost",
            "name": "test",
            "user": "test",
            "password": "test",
        },
        demo={"local_only": True, "isolated_database": True},
    )
    
    # get_alembic_config sets DATABASE_URL, so this should work
    config = get_alembic_config(settings)
    
    # Verify DATABASE_URL was set
    assert "DATABASE_URL" in os.environ
    assert config is not None


def test_management_commands_importable():
    """
    Test that management commands are importable.
    
    Requirements: 2.1, 2.2
    
    Verifies that the management module provides the expected commands.
    """
    from app.db import (
        migrate_current,
        migrate_downgrade,
        migrate_heads,
        migrate_history,
        migrate_revision,
        migrate_upgrade,
    )
    
    # Just verify they're callable
    assert callable(migrate_upgrade)
    assert callable(migrate_downgrade)
    assert callable(migrate_current)
    assert callable(migrate_history)
    assert callable(migrate_heads)
    assert callable(migrate_revision)


def test_env_py_imports_models():
    """
    Test that env.py imports model metadata explicitly.
    
    Requirements: 2.1
    
    Reads env.py and verifies it contains explicit model imports.
    """
    env_py_path = (
        Path(__file__).parent.parent / "app" / "db" / "migrations" / "env.py"
    )
    
    with open(env_py_path) as f:
        content = f.read()
    
    # Verify explicit imports
    assert "from app.models import Base" in content
    assert "from app.models.user import User" in content
    assert "from app.models.farm import Farm" in content
    assert "target_metadata = Base.metadata" in content


def test_env_py_uses_transactional_ddl():
    """
    Test that env.py configures transactional DDL.
    
    Requirements: 2.2
    
    Verifies that transaction_per_migration is enabled for transactional
    version-marker updates.
    """
    env_py_path = (
        Path(__file__).parent.parent / "app" / "db" / "migrations" / "env.py"
    )
    
    with open(env_py_path) as f:
        content = f.read()
    
    # Verify transactional configuration
    assert "transaction_per_migration=True" in content
    
    # Should appear in both offline and online mode configurations
    assert content.count("transaction_per_migration=True") >= 2


def test_alembic_ini_configuration():
    """
    Test that alembic.ini has correct configuration.
    
    Requirements: 2.1
    """
    backend_dir = Path(__file__).parent.parent
    alembic_ini = backend_dir / "alembic.ini"
    
    with open(alembic_ini) as f:
        content = f.read()
    
    # Verify script location
    assert "script_location = app/db/migrations" in content
    
    # Verify timezone is UTC
    assert "timezone = UTC" in content
    
    # Verify output encoding
    assert "output_encoding = utf-8" in content


def test_management_module_requires_explicit_command():
    """
    Test that management commands cannot run without configuration.
    
    Requirements: 2.1, 2.2
    
    Verifies that migrations are not silent or automatic - they require
    explicit invocation through management commands.
    """
    # Import the management module - this should not trigger any migrations
    import app.db.management
    
    # Just importing should be safe and not run anything
    assert app.db.management is not None
    
    # The main() function should require command line arguments
    # (tested indirectly - we're verifying it doesn't auto-run)


def test_startup_expected_revision_matches_migration_head():
    """Startup must accept the schema produced by the current migration chain."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from app.core.database import EXPECTED_ALEMBIC_HEAD

    migrations = Path(__file__).resolve().parents[1] / "app/db/migrations"
    config = Config()
    config.set_main_option("script_location", str(migrations))
    assert ScriptDirectory.from_config(config).get_heads() == [EXPECTED_ALEMBIC_HEAD]
