"""add keirin.wt_races.cup_grade / cup_name（開催グレード GP/GI/GII/GIII/FI/FII）

🔴 既存の `wt_races.grade` は**級班**（A級/S級/L級）で、開催グレードではない。
   そのため GI 開催かどうかを判別できず、2026-08-13 のオールスター競輪（松山）で
   **6R〜11R が丸ごと無推奨**になっていた。実測でこの開催は1レースあたりの
   有償ptが他会場の 5.0倍。

winticket の `cup.grade` / `cup.name` を保存する。**既に取得しているページの
state に入っている**ので追加リクエストは不要（`FETCH_KEIRIN_RACE` の `cups`）。

grade コードの対応は `backend/src/services/keirin_cup_grade.py` が正本。

⚠️ 過去分は NULL。バックフィルは開催（cup_id）単位で1リクエストずつ引けば済む
   （レース単位ではなく開催単位なので、2026年で約280開催）。

Revision ID: 202608141000_keirin
Revises: 202608131000_keirin
Create Date: 2026-08-14
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608141000_keirin"
down_revision = "202608131000_keirin"
branch_labels = None
depends_on = None

SCHEMA = "keirin"
TABLE = "wt_races"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("cup_grade", sa.Integer(), nullable=True), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("cup_name", sa.String(), nullable=True), schema=SCHEMA)
    # 大会だけを引く問い合わせ（穴埋め対象の抽出）が日次で走るのでインデックスを張る。
    op.create_index("ix_wt_races_cup_grade", TABLE, ["cup_grade"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_wt_races_cup_grade", table_name=TABLE, schema=SCHEMA)
    op.drop_column(TABLE, "cup_name", schema=SCHEMA)
    op.drop_column(TABLE, "cup_grade", schema=SCHEMA)
