"""Add last_margin_index to chihou.calculated_indices

Revision ID: u1v2w3x4y5z6
Revises: t0u1v2w3x4y5
Create Date: 2026-04-15
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "u1v2w3x4y5z6"
down_revision: str = "t0u1v2w3x4y5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "calculated_indices",
        sa.Column(
            "last_margin_index",
            sa.Float(),
            nullable=True,
            comment="前走着差指数（0-100, 前走タイム差が小さいほど高評価, 接戦=高評価）",
        ),
        schema="chihou",
    )


def downgrade() -> None:
    op.drop_column("calculated_indices", "last_margin_index", schema="chihou")
