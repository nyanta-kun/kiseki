"""add pred_win_pct / pred_top3_pct to keirin wt_entries (web index display)

Web上のEntryTableで単勝指数・複勝指数（それぞれlgbm_wt_win / 配信top3モデルの
予測確率×100）を表示するための列。wave-picks-wt生成時に書き込まれる。

Revision ID: n0p1q2r3s4t5
Revises: m9n0p1q2r3s4
Create Date: 2026-07-19
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "n0p1q2r3s4t5"
down_revision = "m9n0p1q2r3s4"
branch_labels = None
depends_on = None

SCHEMA = "keirin"


def upgrade() -> None:
    op.add_column("wt_entries", sa.Column("pred_win_pct", sa.Numeric(5, 1), nullable=True), schema=SCHEMA)
    op.add_column("wt_entries", sa.Column("pred_top3_pct", sa.Numeric(5, 1), nullable=True), schema=SCHEMA)


def downgrade() -> None:
    op.drop_column("wt_entries", "pred_top3_pct", schema=SCHEMA)
    op.drop_column("wt_entries", "pred_win_pct", schema=SCHEMA)
