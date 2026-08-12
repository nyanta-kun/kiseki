"""add keirin.netkeirin_submissions.is_confident / confident_ev（自信ありの1日1枠）

netkeirin の「自信あり」アイコンは **1日に1つしか付けられない**。
従来は `CONFIDENT_RANKS = {"7SS"}` として 7SS の入稿すべてに付けていたため、
7SS が複数出た日は**先に入稿したものが取る**＝選んだわけではないレースに付いていた。

ユーザー決定（2026-08-13）: 朝の時点で当日全レースを見て、
**期待値（予測オッズ × Plackett-Luce の三連複的中率）が最も高い1レース**だけに付ける。

選定は `keirin/scripts/pick_confident_race_wt.py` が朝の日次バッチで1回だけ走り、
**その日ちょうど1行**を true にする（他は false へ戻す）。承認/入稿の経路は
この列を見るだけで、自分で選び直さない。

🔴 **1日1件はアプリ側の責務**。部分ユニークインデックスで縛る手もあるが、
   race_key に日付が埋まっているだけで「日」を表す列が無く、
   式インデックス（`substr(race_key,1,8)`）は運用時の理解を難しくするので採らない。
   代わりに選定スクリプトが必ず「当日を全部 false にしてから1件 true」にする。

Revision ID: 202608131000_keirin
Revises: 202608120700_keirin
Create Date: 2026-08-13
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608131000_keirin"
down_revision = "202608120700_keirin"
branch_labels = None
depends_on = None

SCHEMA = "keirin"
TABLE = "netkeirin_submissions"
COLUMN = "is_confident"
# 選定に使った期待値。**確認画面にはこの値を出す**。
# 🔴 既存の表示用 EV（`_expected_value`）とは**別物**。あちらは板のオッズ
#    （欠けた点は予測で補完）を使うが、選定は終日を同じ土俵で比べるため
#    **全点を予測オッズ**で統一している。同じ画面に別の計算の数字を出すと
#    「なぜこのレースが選ばれたのか」が読めなくなるので、選んだ値を保存する。
EV_COLUMN = "confident_ev"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column(COLUMN, sa.Boolean(), nullable=False, server_default=sa.false()),
        schema=SCHEMA,
    )
    op.add_column(
        TABLE,
        sa.Column(EV_COLUMN, sa.Float(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column(TABLE, EV_COLUMN, schema=SCHEMA)
    op.drop_column(TABLE, COLUMN, schema=SCHEMA)
