"""keiba.mekauchida_race_upset 新設 — メカウチダの「荒れやすさ」をレースごとに持つ

## なぜ要るか（2026-10-24 ユーザー要望）

kiseki のレース詳細（中央・地方）に、外部サイト「メカウチダ」が各レースに付ける
「荒れやすさ 低/中/高」を波乱度として表示したい。サイトのページは当日分しか
無く、kiseki はこれまで札を読んでいなかった（買い目だけを読んでいた）。

## 列の約束

- 中央と地方で 1 本。`race_id` は `pillar` によって `keiba.races.id` /
  `chihou.races.id` のどちらかを指す（スキーマをまたぐので FK は張らない）
- 🔴 **発走前の値だけを書く。** 書き手（`services/mekauchida_runner.py`）は
  発走時刻を過ぎたレースを上書きしない。残る値は「kiseki が発走前に最後に読んだ札」（最長10分の取得間隔あり）。
  サイトの履歴ページの札は確定オッズで作り直したものなので、そこから
  バックフィルしてはいけない（前向きの記録でなくなる）

Revision ID: 202610241710_shared
Revises: 202610132300_keirin
Create Date: 2026-10-24
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202610241710_shared"
down_revision = "202610132300_keirin"
branch_labels = None
depends_on = None

SCHEMA = "keiba"
TABLE = "mekauchida_race_upset"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("pillar", sa.String(8), primary_key=True),  # "jra" / "chihou"
        sa.Column("race_id", sa.Integer, primary_key=True),
        sa.Column("level", sa.String(2), nullable=False),  # "低" / "中" / "高"
        sa.Column("site_updated", sa.String(20)),  # サイト表記の「更新 10/24 15:45」
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table(TABLE, schema=SCHEMA)
