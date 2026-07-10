"""add trifecta_payout column to keirin picks_history

trifecta_payout: そのレースで確定した三連単の払戻（100円換算）。
2026-07-10 のランク体系刷新で三連単購入（S/S+ = 7PLUS_ST/STP）が加わったため、
trio_payout（三連複）と並んでレース確定払戻を常に記録する。

Revision ID: h4i5j6k7l8m9
Revises: g3h4i5j6k7l8
Create Date: 2026-07-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "h4i5j6k7l8m9"
down_revision = "g3h4i5j6k7l8"
branch_labels = None
depends_on = None

SCHEMA = "keirin"


def upgrade() -> None:
    op.add_column(
        "picks_history",
        sa.Column("trifecta_payout", sa.Integer(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("picks_history", "trifecta_payout", schema=SCHEMA)
