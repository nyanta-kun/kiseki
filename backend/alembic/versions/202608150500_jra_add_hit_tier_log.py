"""add keiba.hit_tier_races / keiba.hit_tier_picks（JRA 推奨の前向き記録）

hit_tier 推奨（1レース1推奨 = 指数1位馬 + tier S/A/B/C+/C）の運用点を確認するための
記録テーブル。

🔴 **後付けでは作れない**。理由は 3 つとも独立している:

  1. `/api/recommendations` は都度算出で **DB に何も残さない**
  2. `keiba.calculated_indices` の現行 version 行は馬体重到着ごとに上書きされ、
     さらにバックフィルで丸ごと置き換わる（台帳 5.2）
  3. tier の第一分岐 `market_agree`（指数1位が単勝1番人気か）は**発走直前まで動く**。
     実測: 発走10分前の1番人気が確定1番人気と一致するのは **80.7%**（2分前でも 86.5%）。
     確定オッズから tier を再現しても、ユーザーが見た tier とは別物になる

台帳: `docs/jra_rebuild_2026_08.md` 8章 / 14章

Revision ID: 202608150500_jra
Revises: 202608141900_chihou
Create Date: 2026-08-15
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608150500_jra"
down_revision = "202608141900_chihou"
branch_labels = None
depends_on = None

SCHEMA = "keiba"


def upgrade() -> None:
    op.create_table(
        "hit_tier_races",
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
        sa.Column("head_count", sa.Integer(), nullable=True),
        sa.Column("n_entries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_odds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confidence_score", sa.Integer(), nullable=True),
        sa.Column("win_prob_top", sa.Float(), nullable=True),
        sa.Column("entropy_norm", sa.Float(), nullable=True),
        sa.Column("market_agree", sa.Boolean(), nullable=True,
                  comment="指数1位馬が発走前の単勝1番人気と一致するか"),
        sa.Column("top1_win_odds", sa.Float(), nullable=True),
        sa.Column("tier", sa.String(2), nullable=True, comment="S/A/B/C+/C"),
        sa.Column("bet_type", sa.String(10), nullable=True, comment="win/place"),
        sa.Column("is_recommended", sa.Boolean(), nullable=False,
                  server_default=sa.false(), comment="tier が C 以外"),
        sa.Column("skip_reason", sa.String(30), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("n_finishers", sa.Integer(), nullable=True),
        sa.Column("top1_finish_position", sa.Integer(), nullable=True),
        sa.Column("hit", sa.Boolean(), nullable=True,
                  comment="推奨が的中したか。推奨なしのレースは NULL（False と混ぜない）"),
        sa.Column("final_market_agree", sa.Boolean(), nullable=True),
        sa.Column("final_tier", sa.String(2), nullable=True,
                  comment="確定オッズで tier を作り直した値（発走前 tier との比較用）"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["race_id"], [f"{SCHEMA}.races.id"]),
        # 毎分 cron なので同じレースが複数回窓に入る。重複記録の防波堤はここ。
        sa.UniqueConstraint("race_id", name="uq_hit_tier_races_race_id"),
        schema=SCHEMA,
    )
    op.create_index("ix_hit_tier_races_date", "hit_tier_races", ["date"], schema=SCHEMA)
    op.create_index("ix_hit_tier_races_race_id", "hit_tier_races", ["race_id"], schema=SCHEMA)

    op.create_table(
        "hit_tier_picks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pick_race_id", sa.Integer(), nullable=False),
        sa.Column("race_id", sa.Integer(), nullable=False),
        sa.Column("horse_id", sa.Integer(), nullable=True),
        sa.Column("horse_number", sa.Integer(), nullable=False),
        sa.Column("horse_name", sa.String(50), nullable=True),
        sa.Column("composite_index", sa.Numeric(5, 1), nullable=True),
        sa.Column("index_rank", sa.Integer(), nullable=True),
        sa.Column("win_probability", sa.Numeric(6, 4), nullable=True),
        sa.Column("place_probability", sa.Numeric(6, 4), nullable=True),
        sa.Column("out_probability", sa.Numeric(6, 4), nullable=True),
        sa.Column("is_cut_off", sa.Boolean(), nullable=True,
                  comment="Web の足切り候補だったか"),
        sa.Column("pre_win_odds", sa.Float(), nullable=True),
        sa.Column("pre_place_odds", sa.Float(), nullable=True),
        sa.Column("pop_rank", sa.Integer(), nullable=True),
        sa.Column("is_top1", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_recommended", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("finish_position", sa.Integer(), nullable=True),
        sa.Column("abnormality_code", sa.Integer(), nullable=True,
                  comment="0/NULL=正常。1=出走取消 2=発走除外 等"),
        sa.Column("final_win_odds", sa.Float(), nullable=True),
        sa.Column("final_win_popularity", sa.Integer(), nullable=True),
        sa.Column("place_payout_odds", sa.Float(), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["pick_race_id"], [f"{SCHEMA}.hit_tier_races.id"], ondelete="CASCADE"
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_hit_tier_picks_pick_race_id", "hit_tier_picks",
                    ["pick_race_id"], schema=SCHEMA)
    op.create_index("ix_hit_tier_picks_race_id", "hit_tier_picks", ["race_id"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_hit_tier_picks_race_id", "hit_tier_picks", schema=SCHEMA)
    op.drop_index("ix_hit_tier_picks_pick_race_id", "hit_tier_picks", schema=SCHEMA)
    op.drop_table("hit_tier_picks", schema=SCHEMA)
    op.drop_index("ix_hit_tier_races_race_id", "hit_tier_races", schema=SCHEMA)
    op.drop_index("ix_hit_tier_races_date", "hit_tier_races", schema=SCHEMA)
    op.drop_table("hit_tier_races", schema=SCHEMA)
