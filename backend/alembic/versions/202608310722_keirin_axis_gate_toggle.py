"""入稿設定に軸信頼ゲートの ON/OFF を足す

Revision ID: 202608310722_keirin
Revises: 202608310657_keirin
Create Date: 2026-08-31

軸信頼ゲート（`backend/src/services/keirin_type_lab_gate.py`）は
2026-08-27 の導入以来**常時ONの固定**で、画面からは存在も効き具合も見えなかった。
実測（2026-08-31）ではこのゲートだけで当日 89レース → 72レースへ絞っている。

🔴 **既定は true（＝現行どおりゲートあり）**。列を足しただけで挙動が変わらないようにする。
🔴 意味を持つのは `_global` 行だけ（`require_approval` と同じ扱い）。
   ランクごとに切りたくなったら、そのとき列ではなく別テーブルにすること
   （閾値自体が `AXIS_GATE_MIN` でプランごとなので、ON/OFF まで分けると
   「どのプランがどの分位で切られているか」が画面から追えなくなる）。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608310722_keirin"
down_revision = "202608310657_keirin"
branch_labels = None
depends_on = None

SCHEMA = "keirin"
TABLE = "netkeirin_settings"
COLUMN = "axis_gate_enabled"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column(COLUMN, sa.Boolean(), nullable=False, server_default=sa.true()),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column(TABLE, COLUMN, schema=SCHEMA)
