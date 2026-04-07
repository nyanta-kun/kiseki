"""add_chihou_schema

Revision ID: o5p6q7r8s9t0
Revises: m3n4o5p6q7r8
Create Date: 2026-04-06

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "o5p6q7r8s9t0"
down_revision: str = "m3n4o5p6q7r8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """chihou スキーマおよび全テーブルを作成する。"""
    op.execute("CREATE SCHEMA IF NOT EXISTS chihou")

    op.create_table(
        "horses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, comment="馬名"),
        sa.Column("sex", sa.String(10), nullable=True, comment="性別（牡/牝/セン）"),
        sa.Column("birthday", sa.String(8), nullable=True, comment="生年月日（YYYYMMDD）"),
        sa.Column("coat_color", sa.String(20), nullable=True, comment="毛色"),
        sa.Column("owner", sa.String(100), nullable=True, comment="馬主名"),
        sa.Column("breeder", sa.String(100), nullable=True, comment="生産者名"),
        sa.Column(
            "umaconn_code",
            sa.String(20),
            nullable=True,
            unique=True,
            comment="UmaConn馬コード",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
            comment="レコード作成日時",
        ),
        schema="chihou",
    )
    op.create_index("ix_chihou_horses_umaconn_code", "horses", ["umaconn_code"], schema="chihou")

    op.create_table(
        "jockeys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(50), nullable=False, comment="騎手名"),
        sa.Column(
            "umaconn_code",
            sa.String(10),
            nullable=True,
            unique=True,
            comment="UmaConn騎手コード",
        ),
        schema="chihou",
    )
    op.create_index("ix_chihou_jockeys_umaconn_code", "jockeys", ["umaconn_code"], schema="chihou")

    op.create_table(
        "trainers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(50), nullable=False, comment="調教師名"),
        sa.Column(
            "umaconn_code",
            sa.String(10),
            nullable=True,
            unique=True,
            comment="UmaConn調教師コード",
        ),
        schema="chihou",
    )
    op.create_index(
        "ix_chihou_trainers_umaconn_code", "trainers", ["umaconn_code"], schema="chihou"
    )

    op.create_table(
        "races",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("date", sa.String(8), nullable=False, comment="開催日（YYYYMMDD）"),
        sa.Column("course", sa.String(10), nullable=False, comment="競馬場コード"),
        sa.Column("course_name", sa.String(20), nullable=True, comment="競馬場名"),
        sa.Column("race_number", sa.Integer(), nullable=False, comment="レース番号（1-12）"),
        sa.Column("race_name", sa.String(100), nullable=True, comment="レース名"),
        sa.Column("surface", sa.String(5), nullable=True, comment="トラック種別（芝/ダ/障）"),
        sa.Column("distance", sa.Integer(), nullable=True, comment="距離（m）"),
        sa.Column("direction", sa.String(5), nullable=True, comment="回り方向（右/左）"),
        sa.Column("track_type", sa.String(10), nullable=True, comment="トラック種類（内/外/直線）"),
        sa.Column("condition", sa.String(5), nullable=True, comment="馬場状態（良/稍/重/不）"),
        sa.Column("weather", sa.String(10), nullable=True, comment="天候（晴/曇/雨/小雨/雪/小雪）"),
        sa.Column("grade", sa.String(10), nullable=True, comment="グレード（G1/G2/G3/OP/条件等）"),
        sa.Column("head_count", sa.Integer(), nullable=True, comment="出走頭数"),
        sa.Column(
            "registered_count",
            sa.Integer(),
            nullable=True,
            comment="登録頭数（取消前の出走予定頭数）",
        ),
        sa.Column(
            "finishers_count",
            sa.Integer(),
            nullable=True,
            comment="入線頭数（出走頭数から競走中止を除く）",
        ),
        sa.Column("race_type_code", sa.String(2), nullable=True, comment="競走種別コード"),
        sa.Column(
            "weight_type_code",
            sa.String(1),
            nullable=True,
            comment="重量種別コード（1=馬齢,2=定量,3=別定,4=ハンデ）",
        ),
        sa.Column("prize_1st", sa.Integer(), nullable=True, comment="1着本賞金（百円単位）"),
        sa.Column("prize_2nd", sa.Integer(), nullable=True, comment="2着本賞金（百円単位）"),
        sa.Column("prize_3rd", sa.Integer(), nullable=True, comment="3着本賞金（百円単位）"),
        sa.Column("first_3f", sa.Numeric(4, 1), nullable=True, comment="前3ハロン通過タイム（秒）"),
        sa.Column("last_3f_race", sa.Numeric(4, 1), nullable=True, comment="レース後3ハロンタイム（秒）"),
        sa.Column("lap_times", sa.String(75), nullable=True, comment="ラップタイム生データ"),
        sa.Column(
            "record_update_type",
            sa.String(1),
            nullable=True,
            comment="レコード更新区分（0:初期値,1:コース基準更新,2:コースレコード更新）",
        ),
        sa.Column(
            "prev_distance",
            sa.Integer(),
            nullable=True,
            comment="変更前距離（距離変更時のみ設定、単位m）",
        ),
        sa.Column(
            "prev_track_code",
            sa.String(2),
            nullable=True,
            comment="変更前トラックコード（トラック変更時のみ）",
        ),
        sa.Column(
            "prev_grade_code",
            sa.String(1),
            nullable=True,
            comment="変更前グレードコード（グレード変更時のみ）",
        ),
        sa.Column(
            "prev_post_time",
            sa.String(4),
            nullable=True,
            comment="変更前発走時刻（hhmm形式、発走時刻変更時のみ）",
        ),
        sa.Column(
            "post_time",
            sa.String(4),
            nullable=True,
            comment="発走時刻（hhmm形式、例: '1025' = 10:25）",
        ),
        sa.Column(
            "umaconn_race_id",
            sa.String(30),
            nullable=True,
            unique=True,
            comment="UmaConnレースID",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
            comment="レコード作成日時",
        ),
        schema="chihou",
    )
    op.create_index("ix_chihou_races_date", "races", ["date"], schema="chihou")
    op.create_index("ix_chihou_races_umaconn_race_id", "races", ["umaconn_race_id"], schema="chihou")

    op.create_table(
        "race_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "race_id",
            sa.Integer(),
            sa.ForeignKey("chihou.races.id"),
            nullable=False,
        ),
        sa.Column(
            "horse_id",
            sa.Integer(),
            sa.ForeignKey("chihou.horses.id"),
            nullable=False,
        ),
        sa.Column("frame_number", sa.Integer(), nullable=True, comment="枠番（1-8）"),
        sa.Column("horse_number", sa.Integer(), nullable=True, comment="馬番（1-18）"),
        sa.Column(
            "jockey_id",
            sa.Integer(),
            sa.ForeignKey("chihou.jockeys.id"),
            nullable=True,
        ),
        sa.Column(
            "trainer_id",
            sa.Integer(),
            sa.ForeignKey("chihou.trainers.id"),
            nullable=True,
        ),
        sa.Column("weight_carried", sa.Numeric(4, 1), nullable=True, comment="負担重量（kg）"),
        sa.Column("horse_weight", sa.Integer(), nullable=True, comment="馬体重（kg、計不明時はNone）"),
        sa.Column("weight_change", sa.Integer(), nullable=True, comment="馬体重増減（kg、符号付き）"),
        sa.Column("horse_age", sa.Integer(), nullable=True, comment="馬齢（満年齢）"),
        sa.Column(
            "east_west_code",
            sa.String(1),
            nullable=True,
            comment="東西所属コード（1:東,2:西,3:地方,4:海外）",
        ),
        sa.Column(
            "prev_weight_carried",
            sa.Numeric(4, 1),
            nullable=True,
            comment="変更前負担重量（kg、斤量変更時のみ設定）",
        ),
        sa.Column(
            "blinker",
            sa.Boolean(),
            nullable=True,
            comment="ブリンカー使用フラグ（True:使用,False:未使用）",
        ),
        sa.Column(
            "prev_jockey_code",
            sa.String(5),
            nullable=True,
            comment="変更前騎手コード（騎手変更時のみ設定）",
        ),
        sa.Column(
            "jockey_apprentice_code",
            sa.String(1),
            nullable=True,
            comment="騎手見習コード（0:なし,1=5kg減,2=3kg減,3=1kg減）",
        ),
        sa.UniqueConstraint("race_id", "horse_number", name="uq_chihou_race_entry_horse_num"),
        schema="chihou",
    )
    op.create_index("ix_chihou_race_entries_race_id", "race_entries", ["race_id"], schema="chihou")

    op.create_table(
        "race_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "race_id",
            sa.Integer(),
            sa.ForeignKey("chihou.races.id"),
            nullable=False,
        ),
        sa.Column(
            "horse_id",
            sa.Integer(),
            sa.ForeignKey("chihou.horses.id"),
            nullable=False,
        ),
        sa.Column(
            "entry_id",
            sa.Integer(),
            sa.ForeignKey("chihou.race_entries.id"),
            nullable=True,
        ),
        sa.Column("finish_position", sa.Integer(), nullable=True, comment="確定着順"),
        sa.Column("frame_number", sa.Integer(), nullable=True, comment="枠番（1-8）"),
        sa.Column("horse_number", sa.Integer(), nullable=True, comment="馬番（1-18）"),
        sa.Column(
            "jockey_id",
            sa.Integer(),
            sa.ForeignKey("chihou.jockeys.id"),
            nullable=True,
        ),
        sa.Column("weight_carried", sa.Numeric(4, 1), nullable=True, comment="負担重量（kg）"),
        sa.Column("horse_weight", sa.Integer(), nullable=True, comment="馬体重（kg）"),
        sa.Column("weight_change", sa.Integer(), nullable=True, comment="馬体重増減（kg、符号付き）"),
        sa.Column("finish_time", sa.Numeric(6, 1), nullable=True, comment="走破タイム（秒）"),
        sa.Column("margin", sa.Numeric(4, 1), nullable=True, comment="着差（馬身）"),
        sa.Column("passing_1", sa.Integer(), nullable=True, comment="1コーナー通過順位"),
        sa.Column("passing_2", sa.Integer(), nullable=True, comment="2コーナー通過順位"),
        sa.Column("passing_3", sa.Integer(), nullable=True, comment="3コーナー通過順位"),
        sa.Column("passing_4", sa.Integer(), nullable=True, comment="4コーナー通過順位"),
        sa.Column("last_3f", sa.Numeric(3, 1), nullable=True, comment="後3ハロンタイム（秒、個馬別）"),
        sa.Column(
            "abnormality_code",
            sa.Integer(),
            nullable=True,
            comment="異常区分（1:出走取消,2:発走除外,3:競走中止,4:失格,5:降着）",
        ),
        sa.Column(
            "arrival_position",
            sa.Integer(),
            nullable=True,
            comment="入線順位（失格・降着確定前の順位）",
        ),
        sa.Column("dead_heat", sa.Boolean(), nullable=True, comment="同着フラグ（True:同着あり）"),
        sa.Column("margin_code", sa.String(3), nullable=True, comment="着差コード"),
        sa.Column("win_odds", sa.Numeric(5, 1), nullable=True, comment="確定単勝オッズ（倍）"),
        sa.Column(
            "win_popularity",
            sa.Integer(),
            nullable=True,
            comment="単勝人気順位（1位が最低オッズ）",
        ),
        sa.Column("prize_money", sa.Integer(), nullable=True, comment="獲得本賞金（百円単位）"),
        sa.Column("last_4f", sa.Numeric(4, 1), nullable=True, comment="後4ハロンタイム（秒）"),
        sa.Column("time_diff", sa.Numeric(4, 1), nullable=True, comment="1着とのタイム差（秒）"),
        sa.Column("running_style", sa.String(1), nullable=True, comment="脚質（1:逃,2:先,3:差,4:追）"),
        sa.Column(
            "place_odds",
            sa.Numeric(6, 1),
            nullable=True,
            comment="複勝確定払戻倍率（100円あたり払戻÷100）",
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("race_id", "horse_id", name="uq_chihou_race_result_horse"),
        schema="chihou",
    )
    op.create_index("ix_chihou_race_results_race_id", "race_results", ["race_id"], schema="chihou")

    op.create_table(
        "race_payouts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "race_id",
            sa.Integer(),
            sa.ForeignKey("chihou.races.id"),
            nullable=True,
            comment="races テーブルの id",
        ),
        sa.Column(
            "bet_type",
            sa.String(20),
            nullable=False,
            comment="馬券種別（win/place/bracket/quinella/wide/exacta/trio/trifecta）",
        ),
        sa.Column(
            "combination",
            sa.String(30),
            nullable=False,
            comment="馬番組み合わせ（単: '3', 2頭: '3-7', 3頭: '3-7-11' など）",
        ),
        sa.Column(
            "payout",
            sa.Integer(),
            nullable=False,
            comment="払戻金額（100円あたり）",
        ),
        sa.Column("popularity", sa.Integer(), nullable=True, comment="人気順位"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
            comment="レコード作成日時",
        ),
        sa.UniqueConstraint(
            "race_id", "bet_type", "combination", name="uq_chihou_race_payouts_race_type_combo"
        ),
        schema="chihou",
    )
    op.create_index(
        "ix_chihou_race_payouts_race_id", "race_payouts", ["race_id"], schema="chihou"
    )

    op.create_table(
        "odds_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "race_id",
            sa.Integer(),
            sa.ForeignKey("chihou.races.id"),
            nullable=False,
        ),
        sa.Column(
            "bet_type",
            sa.String(20),
            nullable=True,
            comment="賭式（win/place/quinella/trio/trifecta等）",
        ),
        sa.Column(
            "combination",
            sa.String(50),
            nullable=True,
            comment="馬番組み合わせ（単: '3', 連: '3-7', 3連: '3-7-12'）",
        ),
        sa.Column("odds", sa.Numeric(10, 1), nullable=True, comment="オッズ（倍）"),
        sa.Column(
            "fetched_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
            comment="取得日時",
        ),
        schema="chihou",
    )
    op.create_index(
        "ix_chihou_odds_history_race_id", "odds_history", ["race_id"], schema="chihou"
    )

    op.create_table(
        "pedigrees",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "horse_id",
            sa.Integer(),
            sa.ForeignKey("chihou.horses.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("sire", sa.String(100), nullable=True, comment="父馬名"),
        sa.Column("dam", sa.String(100), nullable=True, comment="母馬名"),
        sa.Column("sire_of_dam", sa.String(100), nullable=True, comment="母父馬名"),
        sa.Column("sire_line", sa.String(50), nullable=True, comment="父系統名"),
        sa.Column("dam_sire_line", sa.String(50), nullable=True, comment="母父系統名"),
        sa.UniqueConstraint("horse_id", name="uq_chihou_pedigree_horse_id"),
        schema="chihou",
    )
    op.create_index(
        "ix_chihou_pedigrees_horse_id", "pedigrees", ["horse_id"], schema="chihou"
    )


def downgrade() -> None:
    """chihou スキーマの全テーブルとスキーマ自体を削除する。"""
    op.drop_table("pedigrees", schema="chihou")
    op.drop_table("odds_history", schema="chihou")
    op.drop_table("race_payouts", schema="chihou")
    op.drop_table("race_results", schema="chihou")
    op.drop_table("race_entries", schema="chihou")
    op.drop_table("races", schema="chihou")
    op.drop_table("trainers", schema="chihou")
    op.drop_table("jockeys", schema="chihou")
    op.drop_table("horses", schema="chihou")
    op.execute("DROP SCHEMA IF EXISTS chihou")
