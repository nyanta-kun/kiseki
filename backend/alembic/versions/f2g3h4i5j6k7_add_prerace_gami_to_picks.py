"""add prerace_gami to keirin picks_history

発走直前（15分前）に notify_prerace_wt.py が実測した三連複最安オッズを格納する。
  >= 5.0  → 直前もガミ条件継続（有効推奨）
  <  5.0  → 直前にガミ条件落ち
  NULL    → prerace 通知未到達（夜レース等）

Revision ID: f2g3h4i5j6k7
Revises: e1f2g3h4i5j6
Create Date: 2026-06-18
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "f2g3h4i5j6k7"
down_revision = "e1f2g3h4i5j6"
branch_labels = None
depends_on = None

SCHEMA = "keirin"


def upgrade() -> None:
    op.add_column(
        "picks_history",
        sa.Column("prerace_gami", sa.Numeric(6, 2), nullable=True,
                  comment="発走15分前の三連複最安オッズ(≥5.0=ガミOK/<5.0=条件落ち/NULL=未チェック)"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("picks_history", "prerace_gami", schema=SCHEMA)
