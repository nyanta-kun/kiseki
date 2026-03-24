"""add_full_jvdata_fields

Revision ID: b1c2d3e4f5a6
Revises: a5567aa39d8b
Create Date: 2026-03-24 10:00:00.000000

JVData仕様書の全フィールドをracesおよびrace_entries, race_resultsテーブルに追加する。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "a5567aa39d8b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- races テーブル ---
    op.add_column(
        "races",
        sa.Column(
            "registered_count",
            sa.Integer(),
            nullable=True,
            comment="登録頭数（取消前の出走予定頭数）",
        ),
        schema="keiba",
    )
    op.add_column(
        "races",
        sa.Column(
            "finishers_count",
            sa.Integer(),
            nullable=True,
            comment="入線頭数（出走頭数から競走中止を除く）",
        ),
        schema="keiba",
    )
    op.add_column(
        "races",
        sa.Column(
            "race_type_code",
            sa.String(2),
            nullable=True,
            comment="競走種別コード（コード表2005: 11=2歳,12=3歳,13=3歳以上,14=4歳以上,20=障害等）",
        ),
        schema="keiba",
    )
    op.add_column(
        "races",
        sa.Column(
            "weight_type_code",
            sa.String(1),
            nullable=True,
            comment="重量種別コード（コード表2008: 1=馬齢,2=定量,3=別定,4=ハンデ）",
        ),
        schema="keiba",
    )
    op.add_column(
        "races",
        sa.Column(
            "prize_1st",
            sa.Integer(),
            nullable=True,
            comment="1着本賞金（百円単位）",
        ),
        schema="keiba",
    )
    op.add_column(
        "races",
        sa.Column(
            "prize_2nd",
            sa.Integer(),
            nullable=True,
            comment="2着本賞金（百円単位）",
        ),
        schema="keiba",
    )
    op.add_column(
        "races",
        sa.Column(
            "prize_3rd",
            sa.Integer(),
            nullable=True,
            comment="3着本賞金（百円単位）",
        ),
        schema="keiba",
    )
    op.add_column(
        "races",
        sa.Column(
            "first_3f",
            sa.Numeric(4, 1),
            nullable=True,
            comment="前3ハロン通過タイム（秒、SST形式変換後）",
        ),
        schema="keiba",
    )
    op.add_column(
        "races",
        sa.Column(
            "last_3f_race",
            sa.Numeric(4, 1),
            nullable=True,
            comment="レース後3ハロンタイム（秒、SEのlast_3fは個馬別）",
        ),
        schema="keiba",
    )
    op.add_column(
        "races",
        sa.Column(
            "lap_times",
            sa.String(75),
            nullable=True,
            comment="ラップタイム生データ（25F分×3バイトSST形式、平地のみ）",
        ),
        schema="keiba",
    )
    op.add_column(
        "races",
        sa.Column(
            "record_update_type",
            sa.String(1),
            nullable=True,
            comment="レコード更新区分（0:初期値,1:コース基準更新,2:コースレコード更新）",
        ),
        schema="keiba",
    )
    op.add_column(
        "races",
        sa.Column(
            "prev_distance",
            sa.Integer(),
            nullable=True,
            comment="変更前距離（距離変更時のみ設定、単位m）",
        ),
        schema="keiba",
    )
    op.add_column(
        "races",
        sa.Column(
            "prev_track_code",
            sa.String(2),
            nullable=True,
            comment="変更前トラックコード（トラック変更時のみ）",
        ),
        schema="keiba",
    )
    op.add_column(
        "races",
        sa.Column(
            "prev_grade_code",
            sa.String(1),
            nullable=True,
            comment="変更前グレードコード（グレード変更時のみ）",
        ),
        schema="keiba",
    )
    op.add_column(
        "races",
        sa.Column(
            "prev_post_time",
            sa.String(4),
            nullable=True,
            comment="変更前発走時刻（hhmm形式、発走時刻変更時のみ）",
        ),
        schema="keiba",
    )
    op.alter_column(
        "races",
        "post_time",
        comment="発走時刻（hhmm形式、例: '1025' = 10:25）",
        schema="keiba",
    )

    # --- race_entries テーブル ---
    op.add_column(
        "race_entries",
        sa.Column(
            "horse_age",
            sa.Integer(),
            nullable=True,
            comment="馬齢（満年齢、2001年以降）",
        ),
        schema="keiba",
    )
    op.add_column(
        "race_entries",
        sa.Column(
            "east_west_code",
            sa.String(1),
            nullable=True,
            comment="東西所属コード（1:東,2:西,3:地方,4:海外）",
        ),
        schema="keiba",
    )
    op.add_column(
        "race_entries",
        sa.Column(
            "prev_weight_carried",
            sa.Numeric(4, 1),
            nullable=True,
            comment="変更前負担重量（kg、斤量変更時のみ設定）",
        ),
        schema="keiba",
    )
    op.add_column(
        "race_entries",
        sa.Column(
            "blinker",
            sa.Boolean(),
            nullable=True,
            comment="ブリンカー使用フラグ（True:使用,False:未使用）",
        ),
        schema="keiba",
    )
    op.add_column(
        "race_entries",
        sa.Column(
            "prev_jockey_code",
            sa.String(5),
            nullable=True,
            comment="変更前騎手コード（騎手変更時のみ設定）",
        ),
        schema="keiba",
    )
    op.add_column(
        "race_entries",
        sa.Column(
            "jockey_apprentice_code",
            sa.String(1),
            nullable=True,
            comment="騎手見習コード（0:なし,1=5kg減,2=3kg減,3=1kg減）",
        ),
        schema="keiba",
    )

    # --- race_results テーブル ---
    op.add_column(
        "race_results",
        sa.Column(
            "arrival_position",
            sa.Integer(),
            nullable=True,
            comment="入線順位（失格・降着確定前の順位）",
        ),
        schema="keiba",
    )
    op.add_column(
        "race_results",
        sa.Column(
            "dead_heat",
            sa.Boolean(),
            nullable=True,
            comment="同着フラグ（True:同着あり）",
        ),
        schema="keiba",
    )
    op.add_column(
        "race_results",
        sa.Column(
            "margin_code",
            sa.String(3),
            nullable=True,
            comment="着差コード（コード表2102: '000'=ハナ差等）",
        ),
        schema="keiba",
    )
    op.add_column(
        "race_results",
        sa.Column(
            "win_odds",
            sa.Numeric(5, 1),
            nullable=True,
            comment="確定単勝オッズ（倍、期待値計算の基準値）",
        ),
        schema="keiba",
    )
    op.add_column(
        "race_results",
        sa.Column(
            "win_popularity",
            sa.Integer(),
            nullable=True,
            comment="単勝人気順位（1位が最低オッズ）",
        ),
        schema="keiba",
    )
    op.add_column(
        "race_results",
        sa.Column(
            "prize_money",
            sa.Integer(),
            nullable=True,
            comment="獲得本賞金（百円単位）",
        ),
        schema="keiba",
    )
    op.add_column(
        "race_results",
        sa.Column(
            "last_4f",
            sa.Numeric(4, 1),
            nullable=True,
            comment="後4ハロンタイム（秒）",
        ),
        schema="keiba",
    )
    op.add_column(
        "race_results",
        sa.Column(
            "time_diff",
            sa.Numeric(4, 1),
            nullable=True,
            comment="1着とのタイム差（秒、マイナスは1着馬が速い）",
        ),
        schema="keiba",
    )
    op.add_column(
        "race_results",
        sa.Column(
            "running_style",
            sa.String(1),
            nullable=True,
            comment="JRA判定脚質（1:逃,2:先,3:差,4:追）",
        ),
        schema="keiba",
    )


def downgrade() -> None:
    # --- race_results テーブル ---
    op.drop_column("race_results", "running_style", schema="keiba")
    op.drop_column("race_results", "time_diff", schema="keiba")
    op.drop_column("race_results", "last_4f", schema="keiba")
    op.drop_column("race_results", "prize_money", schema="keiba")
    op.drop_column("race_results", "win_popularity", schema="keiba")
    op.drop_column("race_results", "win_odds", schema="keiba")
    op.drop_column("race_results", "margin_code", schema="keiba")
    op.drop_column("race_results", "dead_heat", schema="keiba")
    op.drop_column("race_results", "arrival_position", schema="keiba")

    # --- race_entries テーブル ---
    op.drop_column("race_entries", "jockey_apprentice_code", schema="keiba")
    op.drop_column("race_entries", "prev_jockey_code", schema="keiba")
    op.drop_column("race_entries", "blinker", schema="keiba")
    op.drop_column("race_entries", "prev_weight_carried", schema="keiba")
    op.drop_column("race_entries", "east_west_code", schema="keiba")
    op.drop_column("race_entries", "horse_age", schema="keiba")

    # --- races テーブル ---
    op.drop_column("races", "prev_post_time", schema="keiba")
    op.drop_column("races", "prev_grade_code", schema="keiba")
    op.drop_column("races", "prev_track_code", schema="keiba")
    op.drop_column("races", "prev_distance", schema="keiba")
    op.drop_column("races", "record_update_type", schema="keiba")
    op.drop_column("races", "lap_times", schema="keiba")
    op.drop_column("races", "last_3f_race", schema="keiba")
    op.drop_column("races", "first_3f", schema="keiba")
    op.drop_column("races", "prize_3rd", schema="keiba")
    op.drop_column("races", "prize_2nd", schema="keiba")
    op.drop_column("races", "prize_1st", schema="keiba")
    op.drop_column("races", "weight_type_code", schema="keiba")
    op.drop_column("races", "race_type_code", schema="keiba")
    op.drop_column("races", "finishers_count", schema="keiba")
    op.drop_column("races", "registered_count", schema="keiba")
