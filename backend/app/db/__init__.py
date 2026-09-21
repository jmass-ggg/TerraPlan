"""
Database migrations and setup.

Requirements: 2.1, 2.2

This package provides:
- Alembic migration configuration (migrations/env.py)
- Management commands for running migrations (management.py)
- Migration scripts (migrations/versions/)

Migrations MUST be run through management commands, never during
requests or automatically at startup.
"""

from .management import (
    migrate_current,
    migrate_downgrade,
    migrate_heads,
    migrate_history,
    migrate_revision,
    migrate_upgrade,
)

__all__ = [
    "migrate_upgrade",
    "migrate_downgrade",
    "migrate_current",
    "migrate_history",
    "migrate_heads",
    "migrate_revision",
]
