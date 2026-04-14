"""Add place_ev_index to chihou.calculated_indices

Revision ID: t0u1v2w3x4y5
Revises: s9t0u1v2w3x4
Create Date: 2026-04-14
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "t0u1v2w3x4y5"
down_revision: str = "s9t0u1v2w3x4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "calculated_indices",
        sa.Column(
            "place_ev_index",
            sa.Numeric(5, 1),
            nullable=True,
            comment=(
                "複勝期待値指数（place_probability × estimated_place_odds, "
                "EV=1.0→50, EV>1.0で期待値プラス, 中立=50）"
            ),
        ),
        schema="chihou",
    )


def downgrade() -> None:
    op.drop_column("calculated_indices", "place_ev_index", schema="chihou")
