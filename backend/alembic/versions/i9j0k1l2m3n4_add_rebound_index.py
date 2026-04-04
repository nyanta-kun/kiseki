"""Add rebound_index to calculated_indices

Revision ID: i9j0k1l2m3n4
Revises: h8i9j0k1l2m3
Create Date: 2026-04-04
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "i9j0k1l2m3n4"
down_revision: str = "h8i9j0k1l2m3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "calculated_indices",
        sa.Column(
            "rebound_index",
            sa.Numeric(5, 1),
            nullable=True,
            comment="巻き返し指数（前走不利+着順乖離から次走巻き返し期待度, 中立=50）",
        ),
        schema="keiba",
    )


def downgrade() -> None:
    op.drop_column("calculated_indices", "rebound_index", schema="keiba")
