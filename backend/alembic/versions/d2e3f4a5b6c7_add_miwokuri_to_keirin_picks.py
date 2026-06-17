"""add miwokuri column to keirin picks_history

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-06-17
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None

SCHEMA = "keirin"


def upgrade() -> None:
    op.add_column(
        "picks_history",
        sa.Column("miwokuri", sa.Boolean(), nullable=False, server_default="false"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("picks_history", "miwokuri", schema=SCHEMA)
