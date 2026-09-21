"""Add ownership-scoped farm creation idempotency.

Revision ID: 0003_farm_idempotency
Revises: 0002_conduit_pipeline
Create Date: 2026-09-07 00:03:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0003_farm_idempotency"
down_revision: Union[str, None] = "0002_conduit_pipeline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "farms",
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "uq_farms_user_idempotency_key",
        "farms",
        ["user_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    # Phase 1 retained farms, but Phase 4 adds explicit deletion. Cascading
    # immutable revisions is required to make the existing circular current-
    # geometry reference deletable as one farm lifecycle operation.
    op.drop_constraint(
        "fk_farm_geometry_revisions_farm_id_farms",
        "farm_geometry_revisions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_farm_geometry_revisions_farm_id_farms",
        "farm_geometry_revisions",
        "farms",
        ["farm_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_farm_geometry_revisions_farm_id_farms",
        "farm_geometry_revisions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_farm_geometry_revisions_farm_id_farms",
        "farm_geometry_revisions",
        "farms",
        ["farm_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_index("uq_farms_user_idempotency_key", table_name="farms")
    op.drop_column("farms", "idempotency_key")
