"""keirin.rider_interviews 新設 — 選手コメント（前検日・レース後）とその分類

## 何を入れるか

winticket のページに埋め込まれた選手コメント（2025-12 以降に本文あり）:

- `kind='pre'`  前検日コメント（開催前日の談話）。racecard ページの
  `FETCH_KEIRIN_INSPECTION_DAY_INTERVIEW_LIST`。**その開催の全レースに使える**
- `kind='post'` レース後コメント。raceresult ページの
  `FETCH_KEIRIN_RACE_RESULT_INTERVIEW_LIST`。**次の出走以降にだけ使える**

分類（調子・トラブル・自己評価など）は claude-haiku-4-5 が付ける。列の意味は
`keirin/scripts/tag_rider_interviews.py` の docstring が正本。

## なぜ別テーブルか

- `wt_entries` は `INSERT OR REPLACE` で取り込みのたびに上書きされる（列を足すと黙って NULL に戻る）
- 1選手1レースに複数（前検日とレース後）あり、公開時刻も別に持つ必要がある

## 時点の扱い（重要）

`src_updated_at`（winticket 側の最終更新時刻）を持つ。表示・特徴量はこれが対象レースの
締切より前のものだけを使う（`docs/type_lab/prereg_rider_condition_2026_10_01.md`）。

Revision ID: 202610010607_shared
Revises: 202609290614_shared
Create Date: 2026-10-01
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202610010607_shared"
down_revision = "202609290614_shared"
branch_labels = None
depends_on = None

SCHEMA = "keirin"
TABLE = "rider_interviews"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(8), nullable=False),  # 'pre' | 'post'
        # 取得したページのレース（pre は開催初日のそのレース・post はそのレース）
        sa.Column("race_key", sa.String(32), nullable=False),
        sa.Column("cup_id", sa.String(16), nullable=True),
        sa.Column("race_date", sa.String(16), nullable=False),
        sa.Column("player_id", sa.Integer, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        # winticket 側の時刻（ページ単位の createdAt / updatedAt）
        sa.Column("src_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("src_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        # ── 分類（未分類は NULL） ──
        sa.Column("condition", sa.SmallInteger, nullable=True),  # -2..2
        sa.Column("trouble", sa.String(8), nullable=True),  # F/I/S/E の並び・無しは 'n'
        sa.Column("fatigue", sa.SmallInteger, nullable=True),  # 0/1
        sa.Column("equipment", sa.SmallInteger, nullable=True),  # 0/1
        sa.Column("self_eval", sa.String(2), nullable=True),  # o/t/m/g/u
        sa.Column("trend", sa.String(2), nullable=True),  # p/d/f/u
        sa.Column("tactic", sa.String(2), nullable=True),  # o/f/s/u
        sa.Column("confidence", sa.SmallInteger, nullable=True),  # -1..1
        sa.Column("tag_model", sa.String(48), nullable=True),
        sa.Column("tagged_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("kind", "race_key", "player_id", name=f"uq_{TABLE}_kind_race_player"),
        schema=SCHEMA,
    )
    op.create_index(f"ix_{TABLE}_player_date", TABLE, ["player_id", "race_date"], schema=SCHEMA)
    op.create_index(f"ix_{TABLE}_cup", TABLE, ["cup_id"], schema=SCHEMA)
    op.create_index(f"ix_{TABLE}_untagged", TABLE, ["id"], schema=SCHEMA, postgresql_where=sa.text("tagged_at IS NULL"))


def downgrade() -> None:
    op.drop_index(f"ix_{TABLE}_untagged", table_name=TABLE, schema=SCHEMA)
    op.drop_index(f"ix_{TABLE}_cup", table_name=TABLE, schema=SCHEMA)
    op.drop_index(f"ix_{TABLE}_player_date", table_name=TABLE, schema=SCHEMA)
    op.drop_table(TABLE, schema=SCHEMA)
