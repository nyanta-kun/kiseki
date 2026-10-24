"""keiba.mekauchida_race_upset に source 列を足す — 履歴ページから取った札を区別する

## なぜ要るか（2026-10-25 ユーザー要望）

一覧・詳細の波乱度を、過去のレースにも出したい。過去分はサイトの履歴ページ
（`/history/YYYYMMDD.html`・`/nar/history/…`）からしか取れない。

ただし履歴ページの札は**確定オッズで作り直したもの**で、利用者が発走前に見た
札とは別物。区別せずに同じ表へ入れると、10/24 から始めた前向きの記録
（発走前に読んだ札）と混ざり、波乱度の精度を測れなくなる。

## 列の約束

- `live`    : 書き手 `services/mekauchida_runner.py` が発走前に読んだ札（既定値）
- `history` : 履歴ページから後で取り込んだ札（確定オッズ時点）
- 🔴 発走前の精度を測るときは `source = 'live'` で絞ること
- 取り込みは `live` の行を上書きしない

Revision ID: 202610251000_shared
Revises: 202610241710_shared
Create Date: 2026-10-25
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202610251000_shared"
down_revision = "202610241710_shared"
branch_labels = None
depends_on = None

SCHEMA = "keiba"
TABLE = "mekauchida_race_upset"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column("source", sa.String(8), nullable=False, server_default="live"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column(TABLE, "source", schema=SCHEMA)
