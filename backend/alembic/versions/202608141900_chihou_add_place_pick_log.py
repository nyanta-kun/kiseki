"""add chihou.place_pick_races / chihou.place_picks（注目馬の前向き記録）

凍結した運用点（発走前6番人気以下 × 指数5位内 × 上位3頭シェア<0.63 × 8頭以上 →
最大2頭）を確認するための記録テーブル。

🔴 **後付けでは作れない**。`chihou.calculated_indices` の現行 version 行は当日
21:30 JST の再算出で上書きされ、そのとき市場特徴の入力が確定オッズに変わる
（台帳 `docs/chihou_rebuild_2026_08.md` 5.4）。日中ユーザーに提示された指数は
DB に残らないため、発走前に撮って保存しない限り検証窓が永久に作れない。

Revision ID: 202608141900_chihou
Revises: 202608141000_keirin
Create Date: 2026-08-14
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608141900_chihou"
down_revision = "202608141000_keirin"
branch_labels = None
depends_on = None

SCHEMA = "chihou"


def upgrade() -> None:
    op.create_table(
        "place_pick_races",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("date", sa.String(8), nullable=False, comment="開催日（YYYYMMDD）"),
        sa.Column("race_id", sa.Integer(), nullable=False),
        sa.Column("course_name", sa.String(20), nullable=True),
        sa.Column("race_number", sa.Integer(), nullable=True),
        sa.Column("post_time", sa.String(4), nullable=True),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lead_minutes", sa.Integer(), nullable=True,
                  comment="発走まで何分の時点で撮ったか"),
        sa.Column("index_version", sa.Integer(), nullable=False),
        sa.Column("rule_version", sa.String(80), nullable=False,
                  comment="判定ルールの署名（閾値を変えたら値が変わる）"),
        sa.Column("head_count_used", sa.Integer(), nullable=True),
        sa.Column("head_count_provisional", sa.Boolean(), nullable=False,
                  server_default=sa.false(),
                  comment="registered_count で代替したか"),
        sa.Column("top3_share", sa.Float(), nullable=True),
        sa.Column("n_entries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_odds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_eligible", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_picked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skip_reason", sa.String(30), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("n_finishers", sa.Integer(), nullable=True),
        sa.Column("race_hit", sa.Boolean(), nullable=True,
                  comment="推奨馬のいずれかが複勝圏"),
        sa.Column("upset_placed", sa.Boolean(), nullable=True,
                  comment="発走前6番人気以下が複勝圏に入ったか（棄権の答え合わせ）"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["race_id"], [f"{SCHEMA}.races.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("race_id", name="uq_chihou_place_pick_races_race"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_chihou_place_pick_races_date", "place_pick_races", ["date"], schema=SCHEMA
    )
    op.create_index(
        "ix_chihou_place_pick_races_race_id", "place_pick_races", ["race_id"], schema=SCHEMA
    )

    op.create_table(
        "place_picks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pick_race_id", sa.Integer(), nullable=False),
        sa.Column("race_id", sa.Integer(), nullable=False),
        sa.Column("horse_id", sa.Integer(), nullable=True),
        sa.Column("horse_number", sa.Integer(), nullable=False),
        sa.Column("horse_name", sa.String(50), nullable=True),
        sa.Column("composite_index", sa.Float(), nullable=True,
                  comment="発走前に提示していた総合指数（上書き前の値）"),
        sa.Column("index_rank", sa.Integer(), nullable=True),
        sa.Column("win_probability", sa.Float(), nullable=True),
        sa.Column("place_probability", sa.Float(), nullable=True),
        sa.Column("pre_win_odds", sa.Float(), nullable=True),
        sa.Column("pre_place_odds", sa.Float(), nullable=True),
        sa.Column("pop_rank", sa.Integer(), nullable=True,
                  comment="発走前オッズによる人気順位（確定人気ではない）"),
        sa.Column("is_eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_picked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pick_order", sa.Integer(), nullable=True),
        sa.Column("finish_position", sa.Integer(), nullable=True),
        sa.Column("abnormality_code", sa.Integer(), nullable=True),
        sa.Column("final_win_odds", sa.Float(), nullable=True),
        sa.Column("final_win_popularity", sa.Integer(), nullable=True),
        sa.Column("place_payout_odds", sa.Float(), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["pick_race_id"], [f"{SCHEMA}.place_pick_races.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["race_id"], [f"{SCHEMA}.races.id"]),
        sa.ForeignKeyConstraint(["horse_id"], [f"{SCHEMA}.horses.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("race_id", "horse_number", name="uq_chihou_place_picks_race_horse"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_chihou_place_picks_pick_race_id", "place_picks", ["pick_race_id"], schema=SCHEMA
    )
    op.create_index(
        "ix_chihou_place_picks_race_id", "place_picks", ["race_id"], schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_index("ix_chihou_place_picks_race_id", table_name="place_picks", schema=SCHEMA)
    op.drop_index("ix_chihou_place_picks_pick_race_id", table_name="place_picks", schema=SCHEMA)
    op.drop_table("place_picks", schema=SCHEMA)
    op.drop_index(
        "ix_chihou_place_pick_races_race_id", table_name="place_pick_races", schema=SCHEMA
    )
    op.drop_index(
        "ix_chihou_place_pick_races_date", table_name="place_pick_races", schema=SCHEMA
    )
    op.drop_table("place_pick_races", schema=SCHEMA)
