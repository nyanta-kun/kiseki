"""add trio_payout column to keirin picks_history

trio_payout: そのレースで確定した三連複の払戻（100円換算）。
picks_history.payout は購入買い目が的中した場合の回収額のみを格納する。
trio_payout は不的中・見送り問わず常にレース確定払戻を記録する。

Revision ID: g3h4i5j6k7l8
Revises: f2g3h4i5j6k7
Create Date: 2026-06-18
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "g3h4i5j6k7l8"
down_revision = "f2g3h4i5j6k7"
branch_labels = None
depends_on = None

SCHEMA = "keirin"


def upgrade() -> None:
    op.add_column(
        "picks_history",
        sa.Column("trio_payout", sa.Integer(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("picks_history", "trio_payout", schema=SCHEMA)
