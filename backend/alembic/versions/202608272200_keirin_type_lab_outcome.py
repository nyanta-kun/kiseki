"""keirin.type_lab_picks に「答え合わせ」用の2列を足す（2026-08-27）

## なぜ足すか

型ラボは事前にレースを6型へ分ける。その分割が当たっているかを見るには
**決着の中身**が要るが、いまの行からは分からない:

- `axis1` / `axis2`（指数1位・2位）はあるが、**指数3位以下の車番が無い**。
  「1着2着が軸で3着が指数3〜4位（＝順当）」と「3着が指数5〜7位（＝穴）」を
  区別できない
- `final_odds` は**的中したときしか入らない**。外れたレースの配当が分からないので
  「荒れ度（arare）が実際の配当を当てているか」を測れない

後から `wt_entries` を引き直して order を作り直すのは**できない**。
モデルが再学習されると p3 が変わり、**その行を作ったときの並びとは別物**になる
（paper は vintage、live は当日の本番モデルで、どちらも後からは再現できない）。
→ 行が作られた時点の並びを焼き付ける。

## 足す列

| 列 | 中身 |
|---|---|
| `p3_order` | 3着内率の降順に並べた車番（例 `"3-1-5-7-2-4-6"`）。`axis1`/`axis2` は先頭2つと一致する |
| `win_tf_odds` | 決着した 1-2-3 の**三連単**確定オッズ（倍）。券種に関係なく**レース単位の荒れ具合**として使う |

🔴 `win_tf_odds` は `final_odds`（＝**買った目**の確定オッズ・的中時のみ）とは別物。
   三連複プラン（D_hit）の行でも三連単のオッズを入れるので、型どうしで配当を比べられる。

Revision ID: 202608272200_keirin
Revises: 202608270930_keirin
Create Date: 2026-08-27
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608272200_keirin"
down_revision = "202608270930_keirin"
branch_labels = None
depends_on = None

SCHEMA = "keirin"
TABLE = "type_lab_picks"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("p3_order", sa.String(32), nullable=True),
                  schema=SCHEMA)
    op.add_column(TABLE, sa.Column("win_tf_odds", sa.Numeric(10, 2), nullable=True),
                  schema=SCHEMA)


def downgrade() -> None:
    op.drop_column(TABLE, "win_tf_odds", schema=SCHEMA)
    op.drop_column(TABLE, "p3_order", schema=SCHEMA)
