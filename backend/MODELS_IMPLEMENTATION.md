# Task 6: Foundation Models Implementation

## Overview

This document summarizes the implementation of Task 6: "Implement constrained foundation models" from the backend-foundation specification.

## Completed Subtasks

### 6.1 Create SQLAlchemy base and mixins ✅

**File:** `app/models/base.py`

Implemented:
- `Base` - SQLAlchemy DeclarativeBase with UUID type mapping
- `UUIDMixin` - Provides UUID primary key with database-generated defaults
- `TimestampMixin` - Provides `created_at` and `updated_at` timestamps for mutable records
- `CreatedAtMixin` - Provides `created_at` timestamp for immutable records
- `OwnershipMixin` - Provides `user_id` reference for owned resources
- Named constraint conventions for stable migrations
- Database UTC timestamp defaults (not application-generated)
- Type annotations for type-safe column definitions

**Requirements satisfied:** 11.1, 11.2, 11.3

### 6.2 Create User and installation metadata models ✅

**File:** `app/models/user.py`

Implemented:
- `User` model:
  - UUID primary key (internal identifier)
  - Unique `(issuer, subject)` constraint for external identity mapping
  - Optional `email` and `display_name` profile fields
  - JSONB `preferences` field with empty object default
  - `created_at` and `updated_at` timestamps
  - Non-empty issuer/subject check constraints
  - Relationship to farms

- `InstallationMetadata` model:
  - Singleton table (enforced via CHECK constraint `id = 1`)
  - `mode` field: `local_demo` or `authenticated`
  - No credentials or personal data stored
  - Used to verify database/auth mode compatibility at startup

**Requirements satisfied:** 1.6, 4.1, 11.1, 11.2, 11.4, 11.6

### 6.3 Create Farm and FarmGeometryRevision models ✅

**File:** `app/models/farm.py`

Implemented:
- `Farm` model:
  - UUID primary key
  - Non-null `user_id` foreign key with RESTRICT deletion
  - Non-blank `name` field (trimmed, not empty)
  - `current_geometry_revision` positive integer
  - `created_at` and `updated_at` timestamps
  - Deferred composite FK to `(farm_id, revision)` for atomic insert
  - Index on `(user_id, created_at, id)` for ownership queries
  - Relationships to user and geometry revisions

- `FarmGeometryRevision` model:
  - UUID primary key
  - Non-null `farm_id` foreign key with RESTRICT deletion
  - Positive `revision` number per farm
  - WGS84/SRID 4326 geometry fields:
    - `geometry` (POLYGON)
    - `centroid` (POINT)
    - `label_point` (POINT)
  - `hectares` field (finite positive float)
  - Unique `(farm_id, revision)` constraint
  - `created_at` timestamp only (immutable records)
  - GiST index on geometry for spatial queries
  - B-tree index on `(farm_id, revision)` for lookups
  - Check constraints for positive revision and valid hectares

**Requirements satisfied:** 2.4, 2.5, 11.1, 11.2, 11.4, 11.5, 11.6, 11.7

## Key Design Decisions

1. **Database-Generated UUIDs**: Using `gen_random_uuid()` server default for security and consistency
2. **Database-Generated Timestamps**: Using `CURRENT_TIMESTAMP AT TIME ZONE 'UTC'` for accuracy
3. **Named Constraints**: Using naming conventions for stable migration diagnostics
4. **Deferred Foreign Keys**: Current geometry revision constraint is deferred to allow atomic farm+revision creation
5. **Immutable Revisions**: Geometry revisions have only `created_at`, no `updated_at`
6. **RESTRICT Deletion**: Explicit restrictive deletion behavior (no cascading deletes yet)
7. **Separate Mixins**: Distinct mixins for different timestamp patterns (mutable vs immutable)

## Database Schema Summary

### Tables Created

1. **users**
   - Primary key: UUID (id)
   - Unique: (issuer, subject)
   - Indexes: Primary key
   - Relationships: One-to-many with farms

2. **installation_metadata**
   - Primary key: Integer (id) - singleton (always 1)
   - Fields: mode (local_demo | authenticated)
   - Purpose: Startup compatibility verification

3. **farms**
   - Primary key: UUID (id)
   - Foreign keys: user_id → users.id
   - Composite FK: (id, current_geometry_revision) → (farm_geometry_revisions.farm_id, revision) [DEFERRED]
   - Indexes: (user_id, created_at, id)
   - Relationships: Belongs-to user, has-many geometry_revisions

4. **farm_geometry_revisions**
   - Primary key: UUID (id)
   - Foreign keys: farm_id → farms.id
   - Unique: (farm_id, revision)
   - Indexes: GiST(geometry), B-tree(farm_id, revision)
   - Relationships: Belongs-to farm

## Files Created

- `farmtwin/backend/app/models/base.py` - Base classes and mixins
- `farmtwin/backend/app/models/user.py` - User and InstallationMetadata models
- `farmtwin/backend/app/models/farm.py` - Farm and FarmGeometryRevision models
- `farmtwin/backend/app/models/__init__.py` - Updated to export all models
- `farmtwin/backend/verify_models.py` - Verification script
- `farmtwin/backend/MODELS_IMPLEMENTATION.md` - This document

## Next Steps

According to the task plan, the following tasks depend on these models:

- **Task 7**: Implement controlled Alembic migrations and database roles
  - Will create the initial migration using these models
  - Will set up PostGIS provisioning
  - Will configure runtime role restrictions

- **Task 10**: Implement ownership-aware repositories and services
  - Will use these models for CRUD operations
  - Will enforce ownership through user_id filtering

## Verification

To verify the models are correctly structured:

```bash
cd farmtwin/backend
python3 verify_models.py
```

Note: Full verification requires database migration (Task 7) and actual PostgreSQL/PostGIS setup.

## Requirements Traceability

All requirements from Task 6 have been satisfied:

- ✅ 1.6 - InstallationMetadata for demo isolation
- ✅ 2.4 - Farm and geometry revision constraints
- ✅ 2.5 - WGS84/SRID 4326 geometry storage
- ✅ 4.1 - User identity mapping
- ✅ 11.1 - UUID primary keys and foreign keys
- ✅ 11.2 - Timezone-aware timestamps
- ✅ 11.3 - Database-enforced update mechanism (timestamps)
- ✅ 11.4 - Non-null ownership references
- ✅ 11.5 - Spatial and ownership indexes
- ✅ 11.6 - Named constraints and explicit deletion behavior
- ✅ 11.7 - Unique (farm_id, revision) and same-farm current-revision reference
