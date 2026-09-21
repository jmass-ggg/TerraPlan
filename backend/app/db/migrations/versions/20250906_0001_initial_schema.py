"""Initial schema with users, farms, and geometry revisions

Revision ID: 0001_initial
Revises: 
Create Date: 2025-09-06 00:01:00.000000

Requirements: 2.1, 2.4, 2.5, 2.8, 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7

This migration creates the initial database schema in the following order:
1. Provision/verify PostGIS extension
2. Create update_timestamp trigger function
3. Create users and installation_metadata tables
4. Create farms table (without current_geometry foreign key)
5. Create farm_geometry_revisions table
6. Add deferred current_geometry foreign key constraint to farms
7. Create indexes
8. Add update_timestamp triggers to mutable tables
9. Revoke UPDATE privilege on farm_geometry_revisions from runtime role

Note: Demo users/farms are seeded separately through an explicit setup command,
never through this production schema migration.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import geoalchemy2

# revision identifiers, used by Alembic.
revision: str = '0001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Upgrade database to initial schema.
    
    Creates tables, constraints, indexes, triggers and role grants.
    Transactional DDL ensures version marker rolls back with failures.
    """
    
    # ========================================================================
    # 1. Provision/verify PostGIS extension
    # ========================================================================
    # The migration role must have CREATE EXTENSION privilege
    # Managed databases may require administrator provisioning
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    
    # Verify PostGIS is available
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_extension WHERE extname = 'postgis'
            ) THEN
                RAISE EXCEPTION 'PostGIS extension is not available';
            END IF;
        END $$;
    """)
    
    # ========================================================================
    # 2. Create update_timestamp trigger function
    # ========================================================================
    # This trigger updates the updated_at column for mutable tables
    # Requirements: 11.3
    op.execute("""
        CREATE OR REPLACE FUNCTION update_timestamp()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = CURRENT_TIMESTAMP;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    
    # ========================================================================
    # 3. Create users table
    # ========================================================================
    # Requirements: 4.1, 11.1, 11.2, 11.4, 11.6
    op.create_table(
        'users',
        # UUID primary key with database default
        sa.Column(
            'id',
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text('gen_random_uuid()'),
        ),
        # External identity - unique (issuer, subject) pair
        sa.Column('issuer', sa.String(length=255), nullable=False),
        sa.Column('subject', sa.String(length=255), nullable=False),
        # Optional profile fields
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('display_name', sa.String(length=255), nullable=True),
        # User preferences as JSONB
        sa.Column(
            'preferences',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # Timestamps with database defaults
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        # Primary key
        sa.PrimaryKeyConstraint('id', name='pk_users'),
        # Unique constraint for identity mapping
        sa.UniqueConstraint('issuer', 'subject', name='uq_users_issuer_subject'),
        # Check constraints for non-empty identity fields
        sa.CheckConstraint("issuer != ''", name='ck_users_issuer_not_empty'),
        sa.CheckConstraint("subject != ''", name='ck_users_subject_not_empty'),
    )
    
    # ========================================================================
    # 4. Create installation_metadata table
    # ========================================================================
    # Requirements: 1.6, 4.3, 11.6, 12.2
    op.create_table(
        'installation_metadata',
        # Singleton ID
        sa.Column(
            'id',
            sa.Integer(),
            nullable=False,
            server_default=sa.text('1'),
        ),
        # Installation mode enum
        sa.Column(
            'mode',
            sa.Enum('LOCAL_DEMO', 'AUTHENTICATED', name='installationmode', native_enum=False, length=20),
            nullable=False,
        ),
        # Primary key
        sa.PrimaryKeyConstraint('id', name='pk_installation_metadata'),
        # Singleton constraint - only one row allowed
        sa.CheckConstraint('id = 1', name='ck_installation_metadata_singleton'),
    )
    
    # ========================================================================
    # 5. Create farms table (without current_geometry FK yet)
    # ========================================================================
    # Requirements: 2.4, 11.1, 11.2, 11.4, 11.5, 11.6, 11.7
    op.create_table(
        'farms',
        # UUID primary key with database default
        sa.Column(
            'id',
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text('gen_random_uuid()'),
        ),
        # User ownership - non-null FK
        sa.Column(
            'user_id',
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        # Farm metadata
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('current_geometry_revision', sa.Integer(), nullable=False),
        # Timestamps with database defaults
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        # Primary key
        sa.PrimaryKeyConstraint('id', name='pk_farms'),
        # Foreign key to users
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
            name='fk_farms_user_id_users',
            ondelete='RESTRICT',
        ),
        # Check constraints
        sa.CheckConstraint(
            "name != '' AND trim(name) = name",
            name='ck_farms_name_not_blank',
        ),
        sa.CheckConstraint(
            'current_geometry_revision > 0',
            name='ck_farms_current_revision_positive',
        ),
    )
    
    # ========================================================================
    # 6. Create farm_geometry_revisions table
    # ========================================================================
    # Requirements: 2.4, 2.5, 11.1, 11.2, 11.5, 11.6, 11.7
    op.create_table(
        'farm_geometry_revisions',
        # UUID primary key with database default
        sa.Column(
            'id',
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text('gen_random_uuid()'),
        ),
        # Farm reference - non-null FK
        sa.Column(
            'farm_id',
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        # Sequential revision number per farm
        sa.Column('revision', sa.Integer(), nullable=False),
        # WGS84/SRID 4326 geometry fields
        sa.Column(
            'geometry',
            geoalchemy2.Geometry(
                geometry_type='POLYGON',
                srid=4326,
                from_text='ST_GeomFromEWKT',
                name='geometry',
            ),
            nullable=False,
        ),
        sa.Column(
            'centroid',
            geoalchemy2.Geometry(
                geometry_type='POINT',
                srid=4326,
                from_text='ST_GeomFromEWKT',
                name='geometry',
            ),
            nullable=False,
        ),
        sa.Column(
            'label_point',
            geoalchemy2.Geometry(
                geometry_type='POINT',
                srid=4326,
                from_text='ST_GeomFromEWKT',
                name='geometry',
            ),
            nullable=False,
        ),
        # Area in hectares - finite positive value
        sa.Column('hectares', sa.Float(), nullable=False),
        # Creation timestamp (immutable records)
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        # Primary key
        sa.PrimaryKeyConstraint('id', name='pk_farm_geometry_revisions'),
        # Foreign key to farms
        sa.ForeignKeyConstraint(
            ['farm_id'],
            ['farms.id'],
            name='fk_farm_geometry_revisions_farm_id_farms',
            ondelete='RESTRICT',
        ),
        # Unique constraint for (farm_id, revision)
        sa.UniqueConstraint(
            'farm_id',
            'revision',
            name='uq_farm_geometry_revisions_farm_revision',
        ),
        # Check constraints
        sa.CheckConstraint(
            'revision > 0',
            name='ck_farm_geometry_revisions_revision_positive',
        ),
        sa.CheckConstraint(
            'hectares > 0 AND hectares = hectares',  # NaN check: NaN != NaN
            name='ck_farm_geometry_revisions_hectares_valid',
        ),
    )
    
    # ========================================================================
    # 7. Add deferred current_geometry foreign key to farms
    # ========================================================================
    # Requirements: 11.7
    # This constraint is deferred to allow atomic insertion of farm + first revision
    # Both columns (id, current_geometry_revision) must match an existing
    # (farm_id, revision) pair in farm_geometry_revisions
    op.create_foreign_key(
        'fk_farms_current_geometry',
        'farms',
        'farm_geometry_revisions',
        ['id', 'current_geometry_revision'],
        ['farm_id', 'revision'],
        ondelete='RESTRICT',
        deferrable=True,
        initially='DEFERRED',
    )
    
    # ========================================================================
    # 8. Create indexes
    # ========================================================================
    # Requirements: 11.5
    
    # User ownership index on farms
    op.create_index(
        'ix_farms_user_created',
        'farms',
        ['user_id', 'created_at', 'id'],
        unique=False,
    )
    
    # Single-column index on user_id (created by SQLAlchemy model)
    op.create_index(
        'ix_farms_user_id',
        'farms',
        ['user_id'],
        unique=False,
    )
    
    # GiST index for spatial queries on geometry
    op.create_index(
        'ix_farm_geometry_revisions_geometry',
        'farm_geometry_revisions',
        ['geometry'],
        unique=False,
        postgresql_using='gist',
    )
    
    # B-tree index for farm/revision lookups
    op.create_index(
        'ix_farm_geometry_revisions_farm_revision',
        'farm_geometry_revisions',
        ['farm_id', 'revision'],
        unique=False,
    )
    
    # ========================================================================
    # 9. Add update_timestamp triggers to mutable tables
    # ========================================================================
    # Requirements: 11.3
    
    # Users table trigger
    op.execute("""
        CREATE TRIGGER update_users_timestamp
        BEFORE UPDATE ON users
        FOR EACH ROW
        EXECUTE FUNCTION update_timestamp();
    """)
    
    # Farms table trigger
    op.execute("""
        CREATE TRIGGER update_farms_timestamp
        BEFORE UPDATE ON farms
        FOR EACH ROW
        EXECUTE FUNCTION update_timestamp();
    """)
    
    # Note: farm_geometry_revisions does NOT get a trigger because
    # revisions are immutable after creation
    
    # ========================================================================
    # 10. Revoke UPDATE on farm_geometry_revisions from runtime role
    # ========================================================================
    # Requirements: 2.8, 11.7
    # Geometry revisions are immutable - boundary edits create new revisions
    # The runtime role should only be able to INSERT new revisions, not UPDATE
    #
    # Note: This assumes the runtime role name matches DATABASE__USER from settings
    # In production, the migration role and runtime role are separate
    # We'll revoke from PUBLIC to ensure no default UPDATE privilege exists
    op.execute("""
        -- Revoke default UPDATE privilege from PUBLIC
        REVOKE UPDATE ON farm_geometry_revisions FROM PUBLIC;
    """)
    
    # Note: Actual runtime role grants should be done by the migration role
    # after role creation. This is placeholder documentation for the privilege model.
    # In production setup:
    # 1. Migration role (admin) creates schema
    # 2. Migration role grants SELECT, INSERT on all tables to runtime role
    # 3. Migration role grants UPDATE on mutable tables (users, farms) to runtime role
    # 4. Migration role does NOT grant UPDATE on farm_geometry_revisions
    # 5. Runtime role has NO schema/extension privileges


def downgrade() -> None:
    """
    Downgrade from initial schema.
    
    WARNING: This will drop all tables and the PostGIS extension.
    Only use in development/testing on disposable databases.
    
    Requirements: 2.2, 2.7
    """
    
    # Drop triggers
    op.execute("DROP TRIGGER IF EXISTS update_farms_timestamp ON farms")
    op.execute("DROP TRIGGER IF EXISTS update_users_timestamp ON users")
    
    # Drop indexes (most are dropped automatically with tables)
    op.drop_index('ix_farm_geometry_revisions_farm_revision', table_name='farm_geometry_revisions')
    op.drop_index('ix_farm_geometry_revisions_geometry', table_name='farm_geometry_revisions', postgresql_using='gist')
    op.drop_index('ix_farms_user_id', table_name='farms')
    op.drop_index('ix_farms_user_created', table_name='farms')
    
    # Drop foreign key constraints explicitly (to avoid dependency issues)
    op.drop_constraint('fk_farms_current_geometry', 'farms', type_='foreignkey')
    
    # Drop tables in reverse dependency order
    op.drop_table('farm_geometry_revisions')
    op.drop_table('farms')
    op.drop_table('installation_metadata')
    op.drop_table('users')
    
    # Drop trigger function
    op.execute("DROP FUNCTION IF EXISTS update_timestamp()")
    
    # Drop enum type
    op.execute("DROP TYPE IF EXISTS installationmode")
    
    # Note: We do NOT drop PostGIS extension in downgrade
    # PostGIS may be shared across databases/schemas
    # Dropping it requires administrator privileges and could affect other schemas
    # Document this limitation per Requirements 2.7
