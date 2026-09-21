# Database Migrations

This directory contains Alembic database migrations for FarmTwin Backend.

**Requirements:** 2.1, 2.2

## Overview

Migrations are managed through Alembic and MUST be run through management commands, never during requests or automatically at startup.

## Configuration

- **alembic.ini**: Main Alembic configuration (located in backend root)
- **env.py**: Migration environment with explicit model imports and transactional DDL
- **script.py.mako**: Template for generating new migration files
- **versions/**: Directory containing migration scripts (created when first migration is generated)

## Key Design Decisions

### Explicit Model Imports
The `env.py` file imports model metadata explicitly:
```python
from app.models import Base
from app.models.user import User, InstallationMetadata
from app.models.farm import Farm, FarmGeometryRevision
```

This ensures all models are registered before migrations run or are generated.

### Transactional DDL
Migrations use `transaction_per_migration=True` to ensure:
- Version markers are updated atomically with schema changes
- Failed migrations roll back the version marker
- Completed earlier revisions remain intact when a later revision fails

### Database URL from Environment
The database URL is never hardcoded. It's set from application settings when running management commands:
```python
os.environ["DATABASE_URL"] = settings.database.get_url()
```

## Management Commands

### Upgrade Database
Apply all pending migrations:
```bash
python -m app.db.management upgrade
```

Apply migrations up to a specific revision:
```bash
python -m app.db.management upgrade <revision>
```

### Check Current Revision
```bash
python -m app.db.management current
```

### View Migration History
```bash
python -m app.db.management history
```

### View Head Revisions
```bash
python -m app.db.management heads
```

### Create New Migration
Create an empty migration:
```bash
python -m app.db.management revision "Description of changes"
```

Auto-generate migration from model changes:
```bash
python -m app.db.management revision "Description of changes" --autogenerate
```

**IMPORTANT:** Always review auto-generated migrations before applying them!

### Downgrade (Development Only)
```bash
python -m app.db.management downgrade <revision>
```

**WARNING:** Downgrades should only be used in development/testing. Production downgrades require careful review and documented procedures.

## Migration Workflow

1. **Make Model Changes**: Update SQLAlchemy models in `app/models/`
2. **Generate Migration**: Run `python -m app.db.management revision "Description" --autogenerate`
3. **Review Generated File**: Check the migration in `versions/` directory
4. **Test Migration**: Apply to a test database and verify schema
5. **Apply to Production**: Run through proper deployment procedures

## Testing

Migration tests verify:
- Empty database upgrade creates correct schema
- Transactional failure rollback works correctly
- Runtime role has restricted privileges
- Constraints, indexes, and triggers are created properly

See `tests/test_alembic_config.py` for configuration tests.

## Safety Notes

- Migrations MUST NOT run during API requests
- Migrations MUST NOT run automatically at startup
- Runtime database role CANNOT run migrations (uses separate admin role)
- Nontransactional operations require documented recovery procedures
- Always test migrations on disposable databases first

## Phase 1 Requirements

Task 7.1 establishes:
- ✅ Alembic configuration under `backend/app/db/migrations/`
- ✅ Explicit model metadata imports
- ✅ Transactional version-marker updates
- ✅ Management commands (no automatic/request-time migrations)

Task 7.2 will create the initial migration with:
- PostGIS provisioning
- User and installation metadata tables
- Farm and geometry revision tables with constraints
- Indexes, triggers, and runtime role grants
