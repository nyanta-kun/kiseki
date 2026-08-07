"""keirin.netkeirin_submissions に bet_detail を追加する

入稿した買い目と1点ごとの金額を JSON で保存する。2026-08-07 に netkeirin 入稿を
「想定着地オッズに応じた傾斜配分」へ変えた（keirin PR#25）結果、

  - 点ごとに金額が違う
  - その金額は**入稿時点の想定オッズ**から決まる

ため、**あとから再現できない**。Web で「入稿時に何をいくらで買ったか」を出すには
入稿の瞬間に保存しておくしかない。

形式（keirin `scripts/netkeirin_submit_wt.py::build_bet_detail` が唯一の生成元）:

    {"total": 10000, "source": "blend",
     "lines": [{"bet_type": "3連複", "combo": "1=2=5", "stake": 4100}, ...]}

`source` は配分の出どころ（blend / odds / model / equal）。均等配分のランクは null。
**買い目は展開済みで持つ**（グループ表記のままだと表示側が展開ロジックを
再実装することになり、買い目の解釈が2箇所に分かれる）。

Revision ID: 202608071800_keirin
Revises: u4v5w6x7y8z9
Create Date: 2026-08-07
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608071800_keirin"
down_revision = "u4v5w6x7y8z9"
branch_labels = None
depends_on = None

SCHEMA = "keirin"
TABLE = "netkeirin_submissions"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column("bet_detail", sa.Text(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column(TABLE, "bet_detail", schema=SCHEMA)
