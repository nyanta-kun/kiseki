"""POG の表示名を pog_group_members へ追加する

統合 Phase 5 の 5d-4。移植した順位表を実際に描画したところ、馬主名が
**「ユーザー18」**と出た。sekito の `users.nickname`（松 / 永 / のださか …）を
引き継いでいなかったため。

## なぜ keiba.users.name ではなく POG 側に持つか

- `keiba.users.name` は **Google の表示名**で、ログインのたびに
  `/api/users/upsert` が Google プロフィールの値で**上書きする**。
  POG のハンドルを入れても初回ログインで消える
- 事前登録した 9 人は未ログインなので `name` が NULL（実測 20人中9人）
- POG のハンドルはゲーム内の呼び名で、本名とは別物

`pog_group_members` に置くのは、指名者が全員メンバー行を持つことを
実測で確認しているため（指名はあるがメンバー行が無い組み合わせ = 0 件）。
年度ごとに持つので、ハンドルを変えた年があっても表現できる。

Revision ID: 202609092204_jra
Revises: 202609092140_shared
Create Date: 2026-09-09
"""

import sqlalchemy as sa

from alembic import op

revision = "202609092204_jra"
down_revision = "202609092140_shared"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pog_group_members",
        sa.Column(
            "nickname", sa.String(length=64), nullable=True,
            comment="POG での表示名（松 / 永 など）。keiba.users.name とは別物",
        ),
        schema="keiba",
    )


def downgrade() -> None:
    op.drop_column("pog_group_members", "nickname", schema="keiba")
