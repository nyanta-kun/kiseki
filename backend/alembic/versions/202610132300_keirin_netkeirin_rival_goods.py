"""keirin.netkeirin_rival_goods 新設 — netkeirin の全予想家の商品成績（1商品=1行）

## なぜ要るか（2026-10-13 ユーザー要望）

自分（二軸探偵・yosoka_id=732）の成績が、netkeirin の予想家全体の中で何位かを
期間を変えて見たい。既存の `netkeirin_sales_*` は**自分の**売上ツールの写しで、
他の予想家は入っていない。

## 出どころ

レース単位の予想一覧 API（`/yoso/api/api_get_goods_list_v4.html?race_id=`）。
1回の取得でそのレースに予想を出した**全予想家**の 購入金額・払戻・収支 が取れる
（予想家の名簿を別に持つ必要がない）。取り込みは `keirin/src/netkeirin_rivals.py`。

## 列の約束

- `payout` / `profit` が **NULL = 未確定**（一覧に払戻欄がまだ出ていない）。
  0 は「外れ」。両者を混ぜない（`keirin_settlement` と同じ考え方）
- 買い目（予想本文）は持たない。詳細ページに「転載はお控えください」とあるため、
  集計に要る金額だけを持つ

Revision ID: 202610132300_keirin
Revises: 202610131900_keirin
Create Date: 2026-10-13
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202610132300_keirin"
down_revision = "202610131900_keirin"
branch_labels = None
depends_on = None

SCHEMA = "keirin"
TABLE = "netkeirin_rival_goods"


def upgrade() -> None:
    op.create_table(
        TABLE,
        # netkeirin の商品ID（例 'b1824725_732'）。同じ予想家が1レースに複数出しうるので
        # (race, 予想家) ではなく商品で一意にする
        sa.Column("goods_id", sa.String(40), primary_key=True),
        sa.Column("race_key", sa.String(30), nullable=False),
        sa.Column("race_date", sa.Date, nullable=False),
        sa.Column("yosoka_id", sa.Integer, nullable=False),
        sa.Column("yosoka_name", sa.String(100)),
        sa.Column("bet", sa.Integer),
        # NULL = 未確定（0 は外れ）
        sa.Column("payout", sa.Integer),
        sa.Column("profit", sa.Integer),
        sa.Column("published_at", sa.DateTime),
        # 一覧の data-sortkey をそのまま（位置の意味は未確定なので解釈しない）
        sa.Column("sortkey_raw", sa.String(200)),
        sa.Column("collected_at", sa.DateTime, server_default=sa.text("NOW()")),
        schema=SCHEMA,
    )
    op.create_index(f"ix_{TABLE}_date_yosoka", TABLE, ["race_date", "yosoka_id"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index(f"ix_{TABLE}_date_yosoka", table_name=TABLE, schema=SCHEMA)
    op.drop_table(TABLE, schema=SCHEMA)
