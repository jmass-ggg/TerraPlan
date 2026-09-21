"""Grant the application role the minimum permissions required at runtime.

Revision ID: 0004_runtime_permissions
Revises: 0003_farm_idempotency
Create Date: 2026-09-07 17:40:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0004_runtime_permissions"
down_revision: Union[str, None] = "0003_farm_idempotency"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Managed installations may name their runtime role differently. The local
    # FarmTwin role is granted only when it exists, keeping the migration usable
    # for schema verification in other environments.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'farmtwin_runtime') THEN
                GRANT USAGE ON SCHEMA public TO farmtwin_runtime;
                GRANT SELECT ON alembic_version, installation_metadata TO farmtwin_runtime;
                GRANT SELECT, INSERT, UPDATE ON users TO farmtwin_runtime;
                GRANT SELECT, INSERT, UPDATE, DELETE ON farms TO farmtwin_runtime;
                GRANT SELECT, INSERT, DELETE ON farm_geometry_revisions TO farmtwin_runtime;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'farmtwin_runtime') THEN
                REVOKE SELECT ON alembic_version, installation_metadata FROM farmtwin_runtime;
                REVOKE SELECT, INSERT, UPDATE ON users FROM farmtwin_runtime;
                REVOKE SELECT, INSERT, UPDATE, DELETE ON farms FROM farmtwin_runtime;
                REVOKE SELECT, INSERT, DELETE ON farm_geometry_revisions FROM farmtwin_runtime;
            END IF;
        END
        $$;
        """
    )
