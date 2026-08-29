"""add keirin.netkeirin_submissions の採点結果キャッシュ列（サマリー高速化）

`/keirin/summary` は当日・当月・当年の3期間を出すが、当年ぶんは入稿1,091件を
**毎リクエスト採点し直して**いた（`wt_entries` へ 3,209回・`wt_odds` へ 2,138回の
インデックス参照）。この DB の shared_buffers は 128MB しかなく `wt_odds` は
7.2GB あるため、ページが押し出されると全部ランダム IO になる。

実測（2026-08-29）: `/keirin/summary` 温 0.48〜0.79秒 / 冷 **1.4〜7.0秒**。
当年ぶんの採点だけで冷 1.48秒（read=2,321 blocks）。

着順と確定配当が入った後の採点結果は二度と変わらないので、行へ焼き付ける。
読み書きの規則と「焼いてはいけない条件」は
`backend/src/services/keirin_settlement_cache.py` が正本。

🔴 **keirin 側（SQLite）へは足さない。** この5列は kiseki backend だけが
   読み書きするキャッシュで、keirin の入稿経路は一切触らない。
   keirin の再入稿は `ON CONFLICT DO UPDATE SET`（INSERT に並べた列だけ）なので
   ここは上書きされず、`bet_detail` が変わった場合は指紋の不一致で
   自動的に作り直される。

Revision ID: 202608291900_keirin
Revises: 202608291230_keirin
Create Date: 2026-08-29
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608291900_keirin"
down_revision = "202608291230_keirin"
branch_labels = None
depends_on = None

SCHEMA = "keirin"
TABLE = "netkeirin_submissions"

#: 採点ロジックの世代 + bet_detail の指紋。**これが一致するときだけ**キャッシュを読む。
FP_COLUMN = "settled_fp"
#: 焼き付けた採点結果。`net_hit` は hit と金額から一意に決まるので列を持たない。
VALUE_COLUMNS = ("settled_bet", "settled_payout", "settled_n_combos")
HIT_COLUMN = "settled_hit"
AT_COLUMN = "settled_at"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column(FP_COLUMN, sa.String(64), nullable=True), schema=SCHEMA)
    for col in VALUE_COLUMNS:
        op.add_column(TABLE, sa.Column(col, sa.Integer(), nullable=True), schema=SCHEMA)
    op.add_column(TABLE, sa.Column(HIT_COLUMN, sa.Boolean(), nullable=True), schema=SCHEMA)
    op.add_column(TABLE, sa.Column(AT_COLUMN, sa.DateTime(), nullable=True), schema=SCHEMA)


def downgrade() -> None:
    op.drop_column(TABLE, AT_COLUMN, schema=SCHEMA)
    op.drop_column(TABLE, HIT_COLUMN, schema=SCHEMA)
    for col in reversed(VALUE_COLUMNS):
        op.drop_column(TABLE, col, schema=SCHEMA)
    op.drop_column(TABLE, FP_COLUMN, schema=SCHEMA)
