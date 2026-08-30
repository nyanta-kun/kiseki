"""型ラボに pw_ent（1着率エントロピー）を足す — 型A の穴狙い振り分け用

Revision ID: 202608310657_keirin
Revises: 202608291900_keirin
Create Date: 2026-08-31

🔴 **なぜ列を足すのか。** 入稿時に `wt_entries.pred_win_pct` を引き直すと、
   モデルが再学習されていれば別の値になり、売り分けが再現しない
   （`p3_order` を焼き付けているのと同じ理由）。
🔴 **NULL を許す。** 既存行は NULL のままで、`sell_plans_for` は
   `pw_ent=None` を A_hit へ倒す（＝現行の挙動）。移行で商品は変わらない。
"""
import sqlalchemy as sa

from alembic import op

revision = "202608310657_keirin"
down_revision = "202608291900_keirin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("type_lab_picks", sa.Column("pw_ent", sa.Numeric(), nullable=True),
                  schema="keirin")


def downgrade() -> None:
    op.drop_column("type_lab_picks", "pw_ent", schema="keirin")
