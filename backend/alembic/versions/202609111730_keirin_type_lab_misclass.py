"""keirin.type_lab_picks に「外れの5分類」の列を足す（2026-09-11）

## なぜ足すか

`docs/type_lab/DESIGN.md` 4.3 の5分類（①的中 / ②軸崩壊 / ③帯下決着 / ④a予算 /
④bモデル）は**打ち手が層ごとに正反対**なので、まとめて「表示的中が低い」と
数えると何も直せない。

いまは行にこの分類が無いため、「今日当たらなかった」に対して
**「読みが外れたのか / 買い目が落としたのか / モデルが届かなかったのか」を
その場で答えられない**（`docs/type_lab/recent_drop_2026_09_11.md` は帯の近似で
済ませており、④a/④b を割れていない）。

🔴 **後から作り直せない。** 判定に要る「決着の目の予測オッズ」と「確率順位」は
   **生成時のモデルにしか無い**。モデルを再学習すると別の値になるので、
   `p3_order` / `pw_ent` と同じく**行へ焼き付ける**しかない
   （`202608272200_keirin` と同じ理由）。

## 足す列

| 列 | いつ入る | 中身 |
|---|---|---|
| `band_min_odds` | 生成時 | その商品に実際に適用した帯（`Plan.min_odds`・**代替へ落ちた後**の値）。0 = 帯なし |
| `prob_ranked` | 生成時 | 確率降順 上位 `PROB_TOP_N` 目の `[[目, 予測オッズ], ...]` |
| `win_pred_odds` | 採点時 | 決着の目の**予測**オッズ（`prob_ranked` から引く）。圏外なら NULL |
| `win_prob_rank` | 採点時 | 同・確率順位。圏外なら NULL |
| `miss_class` | 採点時 | `hit` / `read_axis` / `read_band` / `legs_budget` / `legs_model` |

🔴 `win_pred_odds` は `final_odds`（買った目の**確定**オッズ・的中時のみ）とも
   `win_tf_odds`（決着の**確定**三連単オッズ）とも別物。帯は予測オッズで定義されて
   いるので、③の判定は**同じ物差し（予測）**でなければならない。

判定の正本は `keirin/src/type_lab.py::classify_miss`。

Revision ID: 202609111730_keirin
Revises: 202609110930_shared
Create Date: 2026-09-11
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202609111730_keirin"
down_revision = "202609110930_shared"
branch_labels = None
depends_on = None

SCHEMA = "keirin"
TABLE = "type_lab_picks"

COLUMNS = (
    ("band_min_odds", sa.Numeric(8, 2)),
    ("prob_ranked", postgresql.JSONB(astext_type=sa.Text())),
    ("win_pred_odds", sa.Numeric(10, 2)),
    ("win_prob_rank", sa.Integer()),
    ("miss_class", sa.String(12)),
)


def upgrade() -> None:
    for name, type_ in COLUMNS:
        op.add_column(TABLE, sa.Column(name, type_, nullable=True), schema=SCHEMA)
    # 日次の「今日は何が原因だったか」は (race_date, mode, miss_class) で引く。
    op.create_index("ix_type_lab_picks_miss_class", TABLE,
                    ["race_date", "mode", "miss_class"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_type_lab_picks_miss_class", table_name=TABLE, schema=SCHEMA)
    for name, _ in reversed(COLUMNS):
        op.drop_column(TABLE, name, schema=SCHEMA)
