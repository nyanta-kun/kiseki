"""Add rivals_growth_index to calculated_indices

Revision ID: r8s9t0u1v2w3
Revises: q7r8s9t0u1v2
Create Date: 2026-04-11
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "r8s9t0u1v2w3"
down_revision: str = "q7r8s9t0u1v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "calculated_indices",
        sa.Column(
            "rivals_growth_index",
            sa.Numeric(5, 1),
            nullable=True,
            comment="上昇相手指数（過去に負かした相手馬の後続活躍度から競走強度を推定, 中立=50）",
        ),
        schema="keiba",
    )


def downgrade() -> None:
    op.drop_column("calculated_indices", "rivals_growth_index", schema="keiba")
