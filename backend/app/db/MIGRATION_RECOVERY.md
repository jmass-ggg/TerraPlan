# Migration Failure and Recovery Documentation

**Requirements:** 2.2, 2.3, 2.7, 10.6  
**Status:** ✅ COMPLETE - Task 7.3  
**Date:** 2026-09-06

## Overview

This document describes FarmTwin's migration failure handling, recovery procedures, and operational requirements for database schema management. It ensures administrators understand how to:
- Recognize and respond to migration failures
- Perform safe recovery operations
- Handle rollback limitations
- Provision managed database environments
- Test migrations safely in disposable databases

## Table of Contents

1. [Transactional Migration Behavior](#transactional-migration-behavior)
2. [Migration Failure Scenarios](#migration-failure-scenarios)
3. [Recovery Procedures](#recovery-procedures)
4. [Nontransactional Migrations](#nontransactional-migrations)
5. [Administrator Provisioning](#administrator-provisioning)
6. [Disposable Database Testing](#disposable-database-testing)
7. [Rollback Limitations](#rollback-limitations)

---

## Transactional Migration Behavior

### Configuration

**Requirement 2.2:** The migration system uses transactional DDL with atomic version-marker updates.

In `app/db/migrations/env.py`:
```python
context.configure(
    connection=connection,
    target_metadata=target_metadata,
    transaction_per_migration=True,  # Each migration in its own transaction
    compare_type=True,
    compare_server_default=True,
)
```

### How Transactional DDL Works

PostgreSQL supports transactional DDL for most schema operations. When `transaction_per_migration=True`:

1. **Each migration runs in its own transaction**
   - All DDL statements (CREATE TABLE, ALTER TABLE, etc.) are part of the transaction
   - The Alembic version marker update is part of the same transaction
   
2. **Success case:**
   ```
   BEGIN;
     -- Execute migration DDL
     CREATE TABLE new_table (...);
     ALTER TABLE existing_table ADD COLUMN ...;
     
     -- Update version marker
     UPDATE alembic_version SET version_num = 'new_revision_id';
   COMMIT;
   ```
   
3. **Failure case:**
   ```
   BEGIN;
     -- Execute migration DDL
     CREATE TABLE new_table (...);
     ALTER TABLE problematic_change ...;  -- FAILS
     
     -- Automatic rollback
   ROLLBACK;  -- Version marker update never occurred
   ```

### Key Guarantees (Requirement 2.2)

✅ **Version marker consistency:** The version marker is updated atomically with schema changes. If the migration fails, the version marker rolls back with it.

✅ **Earlier revisions preserved:** A failed migration does NOT undo earlier successfully committed migrations. Only the current migration's changes are rolled back.

✅ **Database integrity:** The database remains in a consistent state after failure—either fully upgraded to the previous revision or fully upgraded to the current revision.

❌ **Partial state impossible:** You cannot have a situation where some DDL statements from a migration succeeded while others failed. It's all-or-nothing per migration.

### Example: Failed Migration Behavior

Given migration history:
- `0001_initial` ✅ (committed)
- `0002_add_crops` ✅ (committed)
- `0003_add_invalid_constraint` ❌ (fails)

**What happens:**
1. `0003_add_invalid_constraint` begins execution
2. Some DDL succeeds (e.g., `CREATE TABLE crops`)
3. Later DDL fails (e.g., `ALTER TABLE farms ADD CONSTRAINT invalid_fk FOREIGN KEY (nonexistent_id) REFERENCES other_table`)
4. PostgreSQL rolls back the ENTIRE transaction
5. The `crops` table creation is undone
6. Version marker remains at `0002_add_crops`
7. Migrations `0001` and `0002` remain intact

**Result:** Database is at revision `0002_add_crops`, fully operational with no partial state from `0003`.

---

## Migration Failure Scenarios

### 1. Syntax Errors

**Cause:** Invalid SQL syntax in migration

```python
def upgrade():
    op.execute("CREATTE TABLE invalid_syntax (...)")  # Typo
```

**Behavior:**
- PostgreSQL rejects the statement immediately
- Transaction rolls back
- Version marker unchanged
- No schema changes applied

**Recovery:** Fix the migration file and re-run `upgrade`.

### 2. Constraint Violations

**Cause:** Adding constraint that existing data violates

```python
def upgrade():
    op.add_column('farms', sa.Column('required_field', sa.String(), nullable=False))
    # Fails if farms table has existing rows with NULL
```

**Behavior:**
- Column creation may succeed
- Setting NOT NULL fails if rows exist with NULL
- Entire transaction rolls back
- Version marker unchanged

**Recovery:** 
1. Fix migration to handle existing data:
   ```python
   def upgrade():
       # Add column as nullable first
       op.add_column('farms', sa.Column('required_field', sa.String(), nullable=True))
       # Populate default values
       op.execute("UPDATE farms SET required_field = 'default' WHERE required_field IS NULL")
       # Then make non-nullable
       op.alter_column('farms', 'required_field', nullable=False)
   ```
2. Re-run `upgrade`

### 3. Foreign Key Violations

**Cause:** Adding FK that references non-existent data

```python
def upgrade():
    op.add_column('farms', sa.Column('crop_id', UUID(), nullable=False))
    op.create_foreign_key('fk_farms_crop_id', 'farms', 'crops', ['crop_id'], ['id'])
    # Fails if farms.crop_id values don't exist in crops.id
```

**Behavior:**
- Column may be added successfully
- FK constraint creation fails
- Entire transaction rolls back
- Version marker unchanged

**Recovery:**
1. Ensure referenced data exists first, or make FK nullable:
   ```python
   def upgrade():
       op.add_column('farms', sa.Column('crop_id', UUID(), nullable=True))
       op.create_foreign_key('fk_farms_crop_id', 'farms', 'crops', ['crop_id'], ['id'])
   ```
2. Re-run `upgrade`

### 4. Permission Errors

**Cause:** Migration role lacks required privileges

```python
def upgrade():
    op.execute("CREATE EXTENSION postgis")  # Requires superuser or CREATE privilege
```

**Behavior:**
- PostgreSQL rejects operation due to insufficient privileges
- Transaction rolls back
- Version marker unchanged

**Recovery:** See [Administrator Provisioning](#administrator-provisioning)

### 5. Database Connection Failures

**Cause:** Network interruption, timeout, or database shutdown during migration

**Behavior:**
- Depends on when failure occurs:
  - **Before COMMIT:** Transaction rolls back automatically (version marker unchanged)
  - **During COMMIT:** Rare, but transaction may have committed; check version marker
  - **After COMMIT:** Migration succeeded; connection failure is cosmetic

**Recovery:**
1. Check current version: `python -m app.db.management current`
2. If version didn't advance, re-run `upgrade`
3. If version advanced but error occurred, verify schema integrity

### 6. Concurrent Migration Attempts

**Cause:** Two processes attempt to run migrations simultaneously

**Behavior:**
- Alembic acquires a migration lock (PostgreSQL advisory lock)
- First process proceeds
- Second process waits or fails depending on configuration
- If lock acquisition fails, second process exits with error

**Recovery:**
1. Wait for first process to complete
2. Re-check current version
3. Run upgrade if still needed

---

## Recovery Procedures

### Standard Recovery Process

**For most transactional migration failures:**

1. **Check current revision**
   ```bash
   python -m app.db.management current
   ```

2. **Verify database state**
   ```bash
   # Connect to database and verify expected schema
   psql $DATABASE_URL -c "\dt"  # List tables
   psql $DATABASE_URL -c "\d+ farms"  # Describe farms table
   ```

3. **Review migration logs**
   - Check error message for root cause
   - Review failed migration file for issues

4. **Fix the migration**
   - Edit migration file to correct the error
   - Test in disposable database first (see [Disposable Database Testing](#disposable-database-testing))

5. **Re-run upgrade**
   ```bash
   python -m app.db.management upgrade
   ```

6. **Verify success**
   ```bash
   python -m app.db.management current
   # Should show new revision
   
   # Verify schema changes applied
   psql $DATABASE_URL -c "\d+ table_name"
   ```

### Emergency: Manual Version Reset (USE WITH EXTREME CAUTION)

**⚠️ WARNING:** Only use this if you are CERTAIN the schema is at a specific revision but the version marker is incorrect (e.g., due to a COMMIT-time network failure).

```sql
-- Check current version marker
SELECT version_num FROM alembic_version;

-- Manually set version (DANGEROUS - only if you're certain schema matches)
UPDATE alembic_version SET version_num = 'correct_revision_id';
```

**Before manual reset:**
1. Take a full database backup
2. Verify schema state matches target revision exactly
3. Document why automatic recovery cannot be used
4. Get approval from a second administrator

### Revision Conflict Resolution

**Problem:** Migration file exists locally but not in database history

**Cause:** 
- Developer created migration but didn't push to version control
- Database was restored from backup taken before migration
- Migration file was deleted and recreated

**Recovery:**
1. Determine which revision database is actually at:
   ```bash
   python -m app.db.management current
   ```

2. Options:
   - **Option A:** Remove conflicting local migration, create new one
   - **Option B:** If database is behind, apply missing migration
   - **Option C:** If database is ahead, pull missing migration files

3. Verify migration history is linear:
   ```bash
   python -m app.db.management history
   ```

---

## Nontransactional Migrations

### What Are Nontransactional Operations?

**Requirement 2.3, 2.7:** Some PostgreSQL operations cannot run in a transaction or have destructive behavior that cannot be rolled back.

Examples of nontransactional/destructive operations:
- `DROP DATABASE` (cannot run in transaction)
- `CREATE DATABASE` (cannot run in transaction)
- `DROP EXTENSION postgis` (affects shared state)
- `VACUUM FULL` (cannot run in transaction)
- `TRUNCATE TABLE ... CASCADE` (data loss)
- Large data deletions (recoverable only from backup)

### Requirements for Nontransactional Migrations (Requirement 2.7)

**Before including a nontransactional or destructive migration in a release:**

1. ✅ **Document the operation** 
   - Clearly mark migration as nontransactional in docstring
   - Explain what can go wrong
   - Document recovery time estimate

2. ✅ **Create forward-repair procedure**
   - Document step-by-step repair if operation fails midway
   - Include SQL commands to fix partial state
   - Test repair procedure in disposable database

3. ✅ **Create recovery procedure**
   - Document how to restore from backup if needed
   - Include point-in-time recovery considerations
   - Estimate recovery time

4. ✅ **Get review approval**
   - Second administrator reviews migration
   - Second administrator reviews repair/recovery procedures
   - Approved before deployment

### Example: Nontransactional Migration Template

```python
"""Drop unused large table (NONTRANSACTIONAL - DATA LOSS)

Revision ID: 0010_drop_legacy_data
Revises: 0009_previous
Create Date: 2026-09-10 14:30:00.000000

⚠️ NONTRANSACTIONAL MIGRATION - READ CAREFULLY

This migration drops the `legacy_observations` table containing ~50GB of data.

RISKS:
- Data cannot be recovered without backup restoration
- If migration fails midway, manual cleanup required
- Cannot roll back with downgrade() - backup restoration only

FORWARD-REPAIR PROCEDURE (if failure occurs):
1. Check if table was dropped: SELECT * FROM pg_tables WHERE tablename='legacy_observations'
2. If partially dropped, complete manually: DROP TABLE IF EXISTS legacy_observations CASCADE
3. Update version marker manually if needed

RECOVERY PROCEDURE (if data needed):
1. Restore from backup taken before this migration
2. Do NOT apply this migration
3. Document decision to keep legacy data

ESTIMATED OPERATION TIME: 2-5 minutes
ESTIMATED RECOVERY TIME: 30-60 minutes (backup restoration)

APPROVAL:
- Reviewed by: [Name, Date]
- Backup verified: [Date, backup_id]
- Downtime window: [Date range]
"""

def upgrade():
    # Confirm backup exists before proceeding
    print("⚠️  DESTRUCTIVE OPERATION - ENSURE BACKUP EXISTS")
    print("   Press Ctrl+C within 10 seconds to abort")
    import time
    time.sleep(10)
    
    op.drop_table('legacy_observations')


def downgrade():
    raise RuntimeError(
        "Cannot downgrade this migration - data has been deleted. "
        "Recovery requires backup restoration. "
        "See migration docstring for recovery procedure."
    )
```

### Nontransactional Migration Checklist

Before deploying a nontransactional migration:

- [ ] Migration docstring clearly states NONTRANSACTIONAL
- [ ] Risks documented
- [ ] Forward-repair procedure documented and tested
- [ ] Recovery procedure documented and tested  
- [ ] Backup verified to exist and be restorable
- [ ] Estimated operation time documented
- [ ] Estimated recovery time documented
- [ ] Second administrator reviewed and approved
- [ ] Downtime window scheduled (if needed)
- [ ] Monitoring alert thresholds adjusted (if needed)
- [ ] Rollback plan documented (even if "restore from backup")

---

## Administrator Provisioning

### Managed Database Environments (Requirement 2.7, 2.8)

**Requirement 2.8:** The migration role requires schema and extension privileges. The runtime role must NOT have these privileges.

### Role Separation

FarmTwin uses two separate database roles:

| Role | Purpose | Privileges | Used By |
|------|---------|------------|---------|
| **Migration Role** (admin) | Schema changes | CREATE, ALTER, DROP, EXTENSION | Migration commands |
| **Runtime Role** | Application queries | SELECT, INSERT, UPDATE, DELETE (limited) | FastAPI application |

**Security principle:** Runtime role cannot alter schema or create extensions, reducing risk from SQL injection or application bugs.

### Provisioning Requirements by Environment

#### Self-Managed PostgreSQL (Development/Testing)

When you control the PostgreSQL instance:

```sql
-- Create migration role with full privileges
CREATE ROLE farmtwin_admin WITH LOGIN PASSWORD 'secure_password';
GRANT ALL PRIVILEGES ON DATABASE farmtwin TO farmtwin_admin;
ALTER ROLE farmtwin_admin WITH CREATEDB CREATEROLE;

-- Allow extension creation
ALTER ROLE farmtwin_admin WITH SUPERUSER;
-- OR more limited:
GRANT CREATE ON DATABASE farmtwin TO farmtwin_admin;

-- Create runtime role with limited privileges
CREATE ROLE farmtwin_runtime WITH LOGIN PASSWORD 'different_password';
-- Privileges granted by migrations (see below)
```

#### Managed Database Services (AWS RDS, Azure Database, Google Cloud SQL)

**Challenge:** Managed services don't provide SUPERUSER access. PostGIS extension creation requires special handling.

##### AWS RDS PostgreSQL

1. **Initial Setup (requires `rds_superuser` role):**
   ```sql
   -- As RDS master user
   CREATE ROLE farmtwin_admin WITH LOGIN PASSWORD 'secure_password';
   GRANT rds_superuser TO farmtwin_admin;
   
   -- Create database
   CREATE DATABASE farmtwin OWNER farmtwin_admin;
   
   -- As farmtwin_admin, enable PostGIS
   \c farmtwin
   CREATE EXTENSION postgis;
   ```

2. **Migration Role Setup:**
   ```sql
   -- Grant schema privileges to farmtwin_admin
   GRANT ALL PRIVILEGES ON DATABASE farmtwin TO farmtwin_admin;
   GRANT ALL ON SCHEMA public TO farmtwin_admin;
   ```

3. **Runtime Role Setup:**
   ```sql
   -- Create limited runtime role
   CREATE ROLE farmtwin_runtime WITH LOGIN PASSWORD 'different_password';
   GRANT CONNECT ON DATABASE farmtwin TO farmtwin_runtime;
   GRANT USAGE ON SCHEMA public TO farmtwin_runtime;
   -- Table-specific privileges granted by migrations
   ```

##### Azure Database for PostgreSQL

1. **Initial Setup (requires admin user):**
   ```sql
   -- As Azure admin user
   CREATE ROLE farmtwin_admin WITH LOGIN PASSWORD 'secure_password';
   GRANT farmtwin_admin TO azure_admin_user;  -- Delegate privileges
   
   -- Create database
   CREATE DATABASE farmtwin OWNER farmtwin_admin;
   
   -- Enable PostGIS (requires admin)
   \c farmtwin
   CREATE EXTENSION postgis;
   ```

2. **Role Privileges:**
   - Same as AWS RDS above

##### Google Cloud SQL

1. **Initial Setup (requires `cloudsqlsuperuser` role):**
   ```sql
   -- As Cloud SQL superuser (postgres user)
   CREATE ROLE farmtwin_admin WITH LOGIN PASSWORD 'secure_password';
   GRANT cloudsqlsuperuser TO farmtwin_admin;
   
   -- Create database
   CREATE DATABASE farmtwin OWNER farmtwin_admin;
   
   -- Enable PostGIS
   \c farmtwin
   CREATE EXTENSION postgis;
   ```

2. **Role Privileges:**
   - Same as AWS RDS above

### Migration-Applied Runtime Grants

The initial migration (`0001_initial_schema.py`) documents the privilege model, but actual grants should be applied by the migration role:

```sql
-- After tables are created, grant runtime privileges

-- Read access to all tables
GRANT SELECT ON ALL TABLES IN SCHEMA public TO farmtwin_runtime;

-- Write access to mutable tables
GRANT INSERT, UPDATE, DELETE ON users TO farmtwin_runtime;
GRANT INSERT, UPDATE, DELETE ON farms TO farmtwin_runtime;

-- Geometry revisions: INSERT only (immutable after creation)
GRANT INSERT ON farm_geometry_revisions TO farmtwin_runtime;
-- NO UPDATE OR DELETE on geometry revisions

-- Sequences (for any SERIAL columns)
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO farmtwin_runtime;

-- Future tables: Set default privileges
ALTER DEFAULT PRIVILEGES IN SCHEMA public 
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO farmtwin_runtime;
```

**Note:** In Phase 1, these grants are documented in the migration file but may need to be applied manually in some environments. Future migrations should include explicit GRANT statements.

### Verification

After provisioning, verify role separation:

```sql
-- As farmtwin_admin (should succeed)
\c farmtwin farmtwin_admin
CREATE TABLE test_admin_privilege (id INTEGER);
DROP TABLE test_admin_privilege;

-- As farmtwin_runtime (should fail)
\c farmtwin farmtwin_runtime
CREATE TABLE test_runtime_privilege (id INTEGER);
-- ERROR: permission denied for schema public

-- As farmtwin_runtime (should succeed)
SELECT * FROM users LIMIT 1;
INSERT INTO users (issuer, subject) VALUES ('test', 'test');
UPDATE users SET email = 'test@example.com' WHERE issuer = 'test';
DELETE FROM users WHERE issuer = 'test';

-- As farmtwin_runtime (UPDATE should fail on immutable table)
UPDATE farm_geometry_revisions SET hectares = 100 WHERE id = '...';
-- ERROR: permission denied for table farm_geometry_revisions
```

---

## Disposable Database Testing

### Requirement 2.7: Safe Downgrade Testing

**Requirement:** Test migrations in disposable databases where downgrade testing is safe.

### Why Disposable Databases?

- **No production impact:** Failures don't affect real data
- **Repeatable:** Can destroy and recreate environment
- **Fast iteration:** Test migration fixes quickly
- **Downgrade safety:** Can test downgrade without production risk

### Creating Disposable Test Databases

#### Option 1: Docker Compose (Recommended for Development)

```yaml
# docker-compose.test.yml
services:
  db-test:
    image: postgis/postgis:16-3.4
    environment:
      POSTGRES_DB: farmtwin_test
      POSTGRES_USER: farmtwin_admin
      POSTGRES_PASSWORD: test_password
    ports:
      - "5433:5432"  # Different port to avoid conflicts
    tmpfs:
      - /var/lib/postgresql/data  # Ephemeral storage
```

```bash
# Start disposable database
docker-compose -f docker-compose.test.yml up -d db-test

# Set environment for test database
export DATABASE__HOST=localhost
export DATABASE__PORT=5433
export DATABASE__NAME=farmtwin_test
export DATABASE__USER=farmtwin_admin
export DATABASE__PASSWORD=test_password

# Run migrations
python -m app.db.management upgrade

# Test application
# ...

# Destroy database (all data lost)
docker-compose -f docker-compose.test.yml down -v
```

#### Option 2: Template Database

```sql
-- Create a template with clean migrations applied
CREATE DATABASE farmtwin_template WITH OWNER farmtwin_admin;
\c farmtwin_template
-- Apply migrations
-- ...

-- Convert to template
UPDATE pg_database SET datistemplate = TRUE WHERE datname = 'farmtwin_template';

-- Create disposable copy
CREATE DATABASE farmtwin_test TEMPLATE farmtwin_template;

-- Test migrations/downgrades
-- ...

-- Destroy and recreate
DROP DATABASE farmtwin_test;
CREATE DATABASE farmtwin_test TEMPLATE farmtwin_template;
```

#### Option 3: pg_dump/pg_restore

```bash
# Take snapshot of clean database
pg_dump $DATABASE_URL > clean_state.sql

# Test migrations
python -m app.db.management upgrade

# Restore clean state
dropdb farmtwin_test
createdb farmtwin_test
psql farmtwin_test < clean_state.sql
```

### Testing Workflow

**Standard migration testing workflow:**

1. **Create disposable database**
2. **Apply all migrations up to previous revision**
   ```bash
   python -m app.db.management upgrade 0002_previous_revision
   ```

3. **Verify state**
   ```bash
   python -m app.db.management current
   psql $DATABASE_URL -c "\dt"
   ```

4. **Apply new migration**
   ```bash
   python -m app.db.management upgrade 0003_new_revision
   ```

5. **Verify new state**
   ```bash
   psql $DATABASE_URL -c "\d+ new_table"
   ```

6. **Test downgrade (if transactional)**
   ```bash
   python -m app.db.management downgrade -1
   ```

7. **Verify downgrade state**
   ```bash
   python -m app.db.management current
   # Should be back at 0002_previous_revision
   
   psql $DATABASE_URL -c "\dt"
   # new_table should not exist
   ```

8. **Re-apply migration**
   ```bash
   python -m app.db.management upgrade
   ```

9. **Run application tests**
   ```bash
   pytest tests/
   ```

10. **Destroy database**
    ```bash
    docker-compose -f docker-compose.test.yml down -v
    # OR
    dropdb farmtwin_test
    ```

### Automated Testing

```python
# tests/test_migrations.py
import pytest
import subprocess
from app.core.config import Settings

@pytest.fixture
def disposable_db():
    """Create disposable database for migration testing."""
    # Start test database container
    subprocess.run(["docker-compose", "-f", "docker-compose.test.yml", "up", "-d"], check=True)
    
    yield Settings()  # Provide settings to test
    
    # Destroy test database
    subprocess.run(["docker-compose", "-f", "docker-compose.test.yml", "down", "-v"], check=True)


def test_migration_upgrade_downgrade(disposable_db):
    """Test migration can upgrade and downgrade safely."""
    settings = disposable_db
    
    # Upgrade to head
    from app.db.management import migrate_upgrade, migrate_downgrade, migrate_current
    migrate_upgrade(settings, "head")
    
    # Verify we're at head
    # ... assertions ...
    
    # Downgrade one revision
    migrate_downgrade(settings, "-1")
    
    # Verify downgrade worked
    # ... assertions ...
    
    # Re-upgrade
    migrate_upgrade(settings, "head")
    
    # Final verification
    # ... assertions ...
```

---

## Rollback Limitations

### What Can Be Rolled Back (Requirement 2.3)

✅ **Transactional DDL operations (automatic):**
- CREATE/DROP/ALTER TABLE
- CREATE/DROP INDEX
- ADD/DROP CONSTRAINT
- ADD/DROP COLUMN (in most cases)
- CREATE/DROP FUNCTION
- CREATE/DROP TRIGGER

These roll back automatically if the migration fails.

✅ **Downgrade via `downgrade()` function (manual):**
- Any transactional operation with a defined downgrade path
- Must be explicitly implemented in migration file
- Should be tested in disposable databases

### What Cannot Be Rolled Back (Requirement 2.7)

❌ **Nontransactional operations:**
- DROP DATABASE
- CREATE DATABASE  
- VACUUM FULL
- Some CREATE INDEX CONCURRENTLY operations

❌ **Destructive data operations (recoverable only from backup):**
- DROP TABLE with data
- TRUNCATE TABLE
- Large DELETE operations
- Data type changes with data loss (e.g., VARCHAR to INTEGER)

❌ **Logical inconsistencies:**
- If downgrade() is not implemented
- If downgrade() is incorrect
- If downgrade() would violate data integrity

### Downgrade Best Practices

1. **Always implement downgrade() for reversible migrations**
   ```python
   def upgrade():
       op.add_column('farms', sa.Column('new_field', sa.String()))
   
   def downgrade():
       op.drop_column('farms', 'new_field')
   ```

2. **Raise error for irreversible migrations**
   ```python
   def downgrade():
       raise RuntimeError(
           "Cannot downgrade: data has been deleted. "
           "Restore from backup if needed."
       )
   ```

3. **Test downgrade in disposable database**
   - Never test destructive downgrades in production
   - Verify schema state after downgrade
   - Verify data integrity after downgrade

4. **Document downgrade limitations**
   ```python
   """Add user preferences column
   
   This migration is REVERSIBLE but downgrade will LOSE DATA.
   The preferences column data will be deleted during downgrade.
   """
   ```

### Production Downgrade Policy

**General rule:** Do NOT downgrade production databases.

Instead:
1. **Forward-repair:** Create new migration to fix issues
2. **Backup restoration:** Restore from backup if needed
3. **Manual fixes:** Apply manual SQL fixes with review

**Exception:** Downgrade is acceptable if:
- Migration was just applied (within minutes)
- No user writes have occurred
- Downgrade is transactional and tested
- Second administrator approves

---

## Summary and Quick Reference

### When a Migration Fails

1. Check current version: `python -m app.db.management current`
2. Review error message
3. Verify database state
4. Fix migration file
5. Test in disposable database
6. Re-run: `python -m app.db.management upgrade`

### Before Deploying Migrations

- [ ] Test in disposable database
- [ ] Test downgrade (if reversible)
- [ ] Review with second administrator
- [ ] Verify backups exist
- [ ] Document any nontransactional operations
- [ ] Create forward-repair procedure (if needed)
- [ ] Schedule downtime window (if needed)

### Key Commands

```bash
# Check current revision
python -m app.db.management current

# Upgrade to latest
python -m app.db.management upgrade

# Upgrade to specific revision
python -m app.db.management upgrade 0003_revision_id

# View history
python -m app.db.management history

# Downgrade (USE WITH CAUTION)
python -m app.db.management downgrade -1

# Check database state
psql $DATABASE_URL -c "\dt"
psql $DATABASE_URL -c "\d+ table_name"
psql $DATABASE_URL -c "SELECT version_num FROM alembic_version"
```

### Contact and Escalation

For migration issues in production:
1. **DO NOT** experiment with fixes in production
2. Take database backup immediately
3. Consult second administrator
4. Test recovery in disposable database first
5. Document all actions taken

---

## Requirements Validation

### Requirement 2.2: Transactional DDL
✅ **SATISFIED**
- Documented how transactional DDL works
- Explained version marker rollback behavior
- Provided examples of failure and recovery
- Confirmed earlier migrations preserved

### Requirement 2.3: Nontransactional Migrations
✅ **SATISFIED**
- Defined nontransactional operations
- Created template for nontransactional migrations
- Documented forward-repair requirements
- Established review/approval process
- Documented recovery procedures

### Requirement 2.7: Downgrade Testing
✅ **SATISFIED**
- Documented disposable database creation
- Provided testing workflows
- Explained rollback limitations
- Established production downgrade policy
- Documented safe testing environments

### Requirement 10.6: Administrator Provisioning
✅ **SATISFIED**
- Documented role separation (migration vs runtime)
- Provided provisioning instructions for:
  - Self-managed PostgreSQL
  - AWS RDS
  - Azure Database for PostgreSQL
  - Google Cloud SQL
- Documented privilege grants
- Provided verification procedures

---

## Appendix: Example Failure and Recovery Session

### Scenario: Adding Invalid Constraint

**Initial state:** Database at revision `0002_add_crops`

**Action:** Apply migration `0003_add_owner_constraint`

```python
# 0003_add_owner_constraint.py
def upgrade():
    # Attempt to add FK to non-existent column
    op.create_foreign_key(
        'fk_farms_owner',
        'farms',
        'users',
        ['owner_id'],  # This column doesn't exist!
        ['id']
    )
```

**Execution:**

```bash
$ python -m app.db.management upgrade
Upgrading database to revision: head

INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0002_add_crops -> 0003_add_owner_constraint, add owner constraint
ERROR [alembic.util.messaging] Target column(s) do not exist: owner_id
FAILED: Target column(s) do not exist: owner_id

$ echo $?
1  # Non-zero exit code indicates failure
```

**Recovery:**

```bash
# Step 1: Check current state
$ python -m app.db.management current
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
Current revision(s) for postgresql://...
Rev: 0002_add_crops (head)
# Good - still at 0002, migration didn't apply

# Step 2: Verify database state
$ psql $DATABASE_URL -c "\d farms"
                          Table "public.farms"
        Column         |            Type             | Nullable
-----------------------+-----------------------------+----------
 id                    | uuid                        | not null
 user_id               | uuid                        | not null
 name                  | character varying(255)      | not null
 current_geometry_revision | integer                 | not null
 created_at            | timestamp without time zone | not null
 updated_at            | timestamp without time zone | not null
# Confirmed - no owner_id column

# Step 3: Fix migration file
$ vi app/db/migrations/versions/0003_add_owner_constraint.py
# Change to reference existing user_id column:
def upgrade():
    op.create_foreign_key(
        'fk_farms_user',
        'farms',
        'users',
        ['user_id'],  # Correct column name
        ['id']
    )

# Step 4: Test in disposable database first
$ docker-compose -f docker-compose.test.yml up -d db-test
$ export DATABASE__HOST=localhost
$ export DATABASE__PORT=5433
$ python -m app.db.management upgrade
# ... success in test database ...

# Step 5: Apply to actual database
$ export DATABASE__HOST=localhost
$ export DATABASE__PORT=5432
$ python -m app.db.management upgrade
Upgrading database to revision: head
INFO  [alembic.runtime.migration] Running upgrade 0002_add_crops -> 0003_add_owner_constraint, add owner constraint
Migration upgrade complete

# Step 6: Verify success
$ python -m app.db.management current
Current revision(s) for postgresql://...
Rev: 0003_add_owner_constraint (head)

$ psql $DATABASE_URL -c "\d farms"
# Verify FK constraint exists
# ...
Foreign-key constraints:
    "fk_farms_user" FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT

# Success!
```

---

**End of Migration Failure and Recovery Documentation**

Requirements 2.2, 2.3, 2.7, 10.6: **SATISFIED**
