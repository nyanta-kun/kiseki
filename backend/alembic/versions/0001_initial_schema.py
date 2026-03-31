"""initial schema - keibaスキーマ全テーブル作成

Revision ID: 0001
Revises:
Create Date: 2026-03-22
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "keiba"


def upgrade() -> None:
    """keibaスキーマと全テーブルを作成する。"""

    # スキーマ作成（存在しない場合のみ）
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    # horses
    op.create_table(
        "horses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("sex", sa.String(10)),
        sa.Column("birthday", sa.String(8)),
        sa.Column("coat_color", sa.String(20)),
        sa.Column("owner", sa.String(100)),
        sa.Column("breeder", sa.String(100)),
        sa.Column("jravan_code", sa.String(20), unique=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_index("ix_horses_jravan_code", "horses", ["jravan_code"], schema=SCHEMA)

    # jockeys
    op.create_table(
        "jockeys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("jravan_code", sa.String(10), unique=True),
        schema=SCHEMA,
    )
    op.create_index("ix_jockeys_jravan_code", "jockeys", ["jravan_code"], schema=SCHEMA)

    # trainers
    op.create_table(
        "trainers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("jravan_code", sa.String(10), unique=True),
        schema=SCHEMA,
    )
    op.create_index("ix_trainers_jravan_code", "trainers", ["jravan_code"], schema=SCHEMA)

    # races
    op.create_table(
        "races",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("date", sa.String(8), nullable=False),
        sa.Column("course", sa.String(10), nullable=False),
        sa.Column("course_name", sa.String(20)),
        sa.Column("race_number", sa.Integer(), nullable=False),
        sa.Column("race_name", sa.String(100)),
        sa.Column("surface", sa.String(5)),
        sa.Column("distance", sa.Integer()),
        sa.Column("direction", sa.String(5)),
        sa.Column("track_type", sa.String(10)),
        sa.Column("condition", sa.String(5)),
        sa.Column("weather", sa.String(10)),
        sa.Column("grade", sa.String(10)),
        sa.Column("head_count", sa.Integer()),
        sa.Column("jravan_race_id", sa.String(30), unique=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_index("ix_races_date", "races", ["date"], schema=SCHEMA)
    op.create_index("ix_races_jravan_race_id", "races", ["jravan_race_id"], schema=SCHEMA)

    # pedigrees
    op.create_table(
        "pedigrees",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("horse_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.horses.id")),
        sa.Column("sire", sa.String(100)),
        sa.Column("dam", sa.String(100)),
        sa.Column("sire_of_dam", sa.String(100)),
        sa.Column("sire_line", sa.String(50)),
        sa.Column("dam_sire_line", sa.String(50)),
        schema=SCHEMA,
    )

    # race_entries
    op.create_table(
        "race_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("race_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.races.id")),
        sa.Column("horse_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.horses.id")),
        sa.Column("frame_number", sa.Integer()),
        sa.Column("horse_number", sa.Integer()),
        sa.Column("jockey_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.jockeys.id")),
        sa.Column("trainer_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.trainers.id")),
        sa.Column("weight_carried", sa.Numeric(4, 1)),
        sa.Column("horse_weight", sa.Integer()),
        sa.Column("weight_change", sa.Integer()),
        sa.UniqueConstraint("race_id", "horse_number", name="uq_race_entry_horse_num"),
        schema=SCHEMA,
    )
    op.create_index("ix_race_entries_race_id", "race_entries", ["race_id"], schema=SCHEMA)

    # race_results
    op.create_table(
        "race_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("race_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.races.id")),
        sa.Column("horse_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.horses.id")),
        sa.Column("entry_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.race_entries.id")),
        sa.Column("finish_position", sa.Integer()),
        sa.Column("frame_number", sa.Integer()),
        sa.Column("horse_number", sa.Integer()),
        sa.Column("jockey_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.jockeys.id")),
        sa.Column("weight_carried", sa.Numeric(4, 1)),
        sa.Column("horse_weight", sa.Integer()),
        sa.Column("weight_change", sa.Integer()),
        sa.Column("finish_time", sa.Numeric(6, 1)),  # 0.1秒単位
        sa.Column("margin", sa.Numeric(4, 1)),
        sa.Column("passing_1", sa.Integer()),
        sa.Column("passing_2", sa.Integer()),
        sa.Column("passing_3", sa.Integer()),
        sa.Column("passing_4", sa.Integer()),
        sa.Column("last_3f", sa.Numeric(3, 1)),  # 0.1秒単位
        sa.Column("abnormality_code", sa.Integer(), default=0),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("race_id", "horse_id", name="uq_race_result_horse"),
        schema=SCHEMA,
    )
    op.create_index("ix_race_results_race_id", "race_results", ["race_id"], schema=SCHEMA)

    # track_conditions
    op.create_table(
        "track_conditions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("date", sa.String(8)),
        sa.Column("course", sa.String(10)),
        sa.Column("surface", sa.String(5)),
        sa.Column("distance", sa.Integer()),
        sa.Column("condition", sa.String(5)),
        sa.Column("bias_value", sa.Numeric(5, 2)),
        schema=SCHEMA,
    )
    op.create_index("ix_track_conditions_date", "track_conditions", ["date"], schema=SCHEMA)

    # calculated_indices
    op.create_table(
        "calculated_indices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("race_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.races.id")),
        sa.Column("horse_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.horses.id")),
        sa.Column("version", sa.Integer(), default=1),
        sa.Column("speed_index", sa.Numeric(5, 1)),
        sa.Column("adjusted_speed_index", sa.Numeric(5, 1)),
        sa.Column("last_3f_index", sa.Numeric(5, 1)),
        sa.Column("course_aptitude", sa.Numeric(5, 1)),
        sa.Column("distance_aptitude", sa.Numeric(5, 1)),
        sa.Column("position_advantage", sa.Numeric(5, 1)),
        sa.Column("jockey_index", sa.Numeric(5, 1)),
        sa.Column("trainer_index", sa.Numeric(5, 1)),
        sa.Column("pedigree_index", sa.Numeric(5, 1)),
        sa.Column("pace_index", sa.Numeric(5, 1)),
        sa.Column("rotation_index", sa.Numeric(5, 1)),
        sa.Column("training_index", sa.Numeric(5, 1)),
        sa.Column("paddock_index", sa.Numeric(5, 1)),
        sa.Column("disadvantage_flag", sa.Boolean(), default=False),
        sa.Column("composite_index", sa.Numeric(5, 1)),
        sa.Column("win_probability", sa.Numeric(5, 4)),
        sa.Column("place_probability", sa.Numeric(5, 4)),
        sa.Column("calculated_at", sa.DateTime(), server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_index("ix_calc_indices_race_id", "calculated_indices", ["race_id"], schema=SCHEMA)

    # entry_changes
    op.create_table(
        "entry_changes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("race_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.races.id")),
        sa.Column("horse_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.horses.id")),
        sa.Column("change_type", sa.String(20)),
        sa.Column("old_value", sa.String(100)),
        sa.Column("new_value", sa.String(100)),
        sa.Column("detected_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("recalc_triggered", sa.Boolean(), default=False),
        schema=SCHEMA,
    )

    # odds_history
    op.create_table(
        "odds_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("race_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.races.id")),
        sa.Column("bet_type", sa.String(20)),
        sa.Column("combination", sa.String(50)),
        sa.Column("odds", sa.Numeric(10, 1)),
        sa.Column("fetched_at", sa.DateTime(), server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_index("ix_odds_history_race_id", "odds_history", ["race_id"], schema=SCHEMA)


def downgrade() -> None:
    """全テーブルを削除する（keibaスキーマは残す）。"""
    op.drop_table("odds_history", schema=SCHEMA)
    op.drop_table("entry_changes", schema=SCHEMA)
    op.drop_table("calculated_indices", schema=SCHEMA)
    op.drop_table("track_conditions", schema=SCHEMA)
    op.drop_table("race_results", schema=SCHEMA)
    op.drop_table("race_entries", schema=SCHEMA)
    op.drop_table("pedigrees", schema=SCHEMA)
    op.drop_table("races", schema=SCHEMA)
    op.drop_table("trainers", schema=SCHEMA)
    op.drop_table("jockeys", schema=SCHEMA)
    op.drop_table("horses", schema=SCHEMA)
