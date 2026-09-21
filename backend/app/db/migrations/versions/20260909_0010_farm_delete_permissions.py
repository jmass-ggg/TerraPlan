"""Grant DELETE on analysis_snapshots and saved_scenarios to runtime role.

Without DELETE on analysis_snapshots the runtime role cannot explicitly remove
snapshot rows during farm deletion, causing a permission DBAPIError that
surfaces as SERVICE_UNAVAILABLE.

saved_scenarios has ondelete=CASCADE on farm_id so the DB engine handles it,
but the runtime role also needs DELETE for any direct cleanup paths.

Revision ID: 0010_farm_delete_permissions
"""
from alembic import op

revision = "0010_farm_delete_permissions"
down_revision = "0009_workflow_integrity"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "farmtwin_runtime"


def upgrade():
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
                GRANT DELETE ON analysis_snapshots TO {RUNTIME_ROLE};
                GRANT DELETE ON saved_scenarios TO {RUNTIME_ROLE};
            END IF;
        END
        $$;
    """)


def downgrade():
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
                REVOKE DELETE ON analysis_snapshots FROM {RUNTIME_ROLE};
                REVOKE DELETE ON saved_scenarios FROM {RUNTIME_ROLE};
            END IF;
        END
        $$;
    """)
