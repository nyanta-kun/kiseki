"""keirin.race_shapes 新設 — **全車数**のレースに型（A〜F）を持たせる

## なぜ要るか

型ラボの型判定（`keirin/src/type_lab.race_shape`）は 3着内率さえあれば車数に
依存せず出せるのに、実際に走っていたのは `build_type_lab_picks.py` の中だけで、
そこは **7車と9車しか組まない**（`type_lab_daily.sh` が `--n-entries 7` と `9` の
2回しか回さないため）。結果として 5車・6車・8車のレースには型が1件も無く、
`/keirin` の一覧で**同じ推奨外なのに型が出る行と出ない行が混ざっていた**
（2026-09-12 実測: 直近1週間で 31レース ＝ 5車2・6車25・8車4）。

## なぜ別テーブルか

- `type_lab_picks` は**売る商品**の器。型だけの行を混ぜると件数・ROI の集計が
  静かに狂う（一意キーも `(race_key, plan_key, mode)` で plan_key が要る）
- `wt_races` へ列を足すのは不可。スクレイパが `INSERT OR REPLACE INTO wt_races`
  で書き戻すので、**取り込みのたびに黙って NULL へ戻る**

## 使い道は表示だけ

買い目も予測オッズも持たない（5車・6車は予測オッズモデル自体が無い）。
商品を出すのは従来どおり 7車・9車のみで、この表は
「このレースはどういう型か」を一覧に出すためだけに使う。

Revision ID: 202609120900_keirin
Revises: 202609110930_shared
Create Date: 2026-09-12
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202609120900_keirin"
down_revision = "202609110930_shared"
branch_labels = None
depends_on = None

SCHEMA = "keirin"
TABLE = "race_shapes"


def upgrade() -> None:
    op.create_table(
        TABLE,
        # 1レース1行。組み直しは UPSERT で上書きする（型は表示専用なので
        # 焼き付けの必要が無い＝`type_lab_picks` と方針が違う）。
        sa.Column("race_key", sa.String(32), primary_key=True),
        sa.Column("race_date", sa.String(16), nullable=False),
        sa.Column("n_entries", sa.Integer, nullable=True),
        # ── 型の根拠（`type_lab.RaceShape` と同じ意味・同じ計算） ──
        sa.Column("type_label", sa.String(2), nullable=False),
        sa.Column("axis_sum", sa.Numeric(8, 4), nullable=True),
        sa.Column("arare", sa.Integer, nullable=True),
        sa.Column("gap", sa.Numeric(8, 4), nullable=True),
        sa.Column("pw_ent", sa.Numeric(10, 6), nullable=True),
        sa.Column("axis1", sa.Integer, nullable=True),
        sa.Column("axis2", sa.Integer, nullable=True),
        sa.Column("p3_order", sa.String(32), nullable=True),
        sa.Column("computed_at", sa.DateTime, server_default=sa.text("NOW()")),
        schema=SCHEMA,
    )
    op.create_index(
        f"ix_{TABLE}_race_date", TABLE, ["race_date"], schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(f"ix_{TABLE}_race_date", table_name=TABLE, schema=SCHEMA)
    op.drop_table(TABLE, schema=SCHEMA)
