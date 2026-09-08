"""POG 指名候補の馬マスタ keiba.pog_horses を追加する（テーブルのみ）

統合 Phase 5（POG 移植）の 5a。sekito.horse(16,926行) の後継。
参照する側（スクレイパ・API）は別 PR で入れる（デプロイは
「コード切替 → マイグレーション」の順のため）。

🔴 sekito.horse が持っていた成績の集計列
（win / place / show / out / prize / prize_jra / prize_other）は**持ち込まない**。
2026-09-08 の実測で、これを更新する処理が存在せず 2024年産 7,855頭は全件 0 のまま
（実際には 2,538頭が出走済み）で、/api/pog/group/:id/stable-ranking などが
総賞金 0 を返していた。成績は race_results から都度導出する。

Revision ID: 202609081739_jra
Revises: 202609081613_shared
Create Date: 2026-09-08
"""

import sqlalchemy as sa

from alembic import op

revision = "202609081739_jra"
down_revision = "202609081613_shared"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pog_horses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "netkeiba_horse_id", sa.String(length=20), nullable=False,
            comment="netkeiba の馬ID。先頭4桁が生産年。指名の結合キー",
        ),
        sa.Column(
            "name", sa.String(length=100), nullable=True,
            comment="馬名（カタカナ）。未命名馬は空のことがある",
        ),
        sa.Column("sex", sa.String(length=4), nullable=True, comment="性別"),
        sa.Column(
            "birth_year", sa.SmallInteger(), nullable=True,
            comment="生産年。世代で引くための列（netkeiba_horse_id 先頭4桁と同じ）",
        ),
        sa.Column("birthday", sa.Date(), nullable=True, comment="生年月日"),
        sa.Column("sire", sa.Text(), nullable=True, comment="父"),
        sa.Column("broodmare", sa.Text(), nullable=True, comment="母"),
        sa.Column("broodmare_sire", sa.Text(), nullable=True, comment="母父"),
        sa.Column("stable", sa.Text(), nullable=True, comment="厩舎（調教師名）"),
        sa.Column("owner", sa.Text(), nullable=True, comment="馬主"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("netkeiba_horse_id", name="uq_pog_horses_netkeiba_id"),
        schema="keiba",
    )
    op.create_index("ix_pog_horses_birth_year", "pog_horses", ["birth_year"], schema="keiba")


def downgrade() -> None:
    op.drop_index("ix_pog_horses_birth_year", table_name="pog_horses", schema="keiba")
    op.drop_table("pog_horses", schema="keiba")
