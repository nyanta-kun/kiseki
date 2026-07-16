"""add res_standing / res_back / final_half to keirin wt_entries

競輪レース単位のS/B取得・上がりタイム（展開予測モデル用・2026-07-17）。

res_standing: このレースでS（スタンディング先頭）を取ったか（0/1・結果確定後に記録）
res_back: このレースでB（バック先頭）を取ったか（0/1・結果確定後に記録）
final_half: 上がりタイム（秒・例 11.9）

Revision ID: k7l8m9n0p1q2
Revises: j6k7l8m9n0p1
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "k7l8m9n0p1q2"
down_revision = "j6k7l8m9n0p1"
branch_labels = None
depends_on = None

SCHEMA = "keirin"


def upgrade() -> None:
    op.add_column(
        "wt_entries", sa.Column("res_standing", sa.Integer(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "wt_entries", sa.Column("res_back", sa.Integer(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "wt_entries", sa.Column("final_half", sa.REAL(), nullable=True), schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_column("wt_entries", "final_half", schema=SCHEMA)
    op.drop_column("wt_entries", "res_back", schema=SCHEMA)
    op.drop_column("wt_entries", "res_standing", schema=SCHEMA)
