"""add_column_comments_to_existing

Revision ID: c3d4e5f6a7b8
Revises: b1c2d3e4f5a6
Create Date: 2026-03-24

初期スキーマ作成時にコメントが設定されていなかった既存カラムに
カラムコメントを追加する。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "keiba"


def upgrade() -> None:
    # --- horses テーブル ---
    op.alter_column("horses", "name", existing_type=sa.String(100), comment="馬名", schema=SCHEMA)
    op.alter_column("horses", "sex", existing_type=sa.String(10), comment="性別（牡/牝/セン）", schema=SCHEMA)
    op.alter_column("horses", "birthday", existing_type=sa.String(8), comment="生年月日（YYYYMMDD）", schema=SCHEMA)
    op.alter_column("horses", "coat_color", existing_type=sa.String(20), comment="毛色", schema=SCHEMA)
    op.alter_column("horses", "owner", existing_type=sa.String(100), comment="馬主名", schema=SCHEMA)
    op.alter_column("horses", "breeder", existing_type=sa.String(100), comment="生産者名", schema=SCHEMA)
    op.alter_column("horses", "jravan_code", existing_type=sa.String(20), comment="JRA-VAN馬コード（10桁）", schema=SCHEMA)
    op.alter_column("horses", "created_at", existing_type=sa.DateTime(), comment="レコード作成日時", schema=SCHEMA)

    # --- pedigrees テーブル ---
    op.alter_column("pedigrees", "sire", existing_type=sa.String(100), comment="父馬名", schema=SCHEMA)
    op.alter_column("pedigrees", "dam", existing_type=sa.String(100), comment="母馬名", schema=SCHEMA)
    op.alter_column("pedigrees", "sire_of_dam", existing_type=sa.String(100), comment="母父馬名", schema=SCHEMA)
    op.alter_column("pedigrees", "sire_line", existing_type=sa.String(50), comment="父系統名", schema=SCHEMA)
    op.alter_column("pedigrees", "dam_sire_line", existing_type=sa.String(50), comment="母父系統名", schema=SCHEMA)

    # --- jockeys テーブル ---
    op.alter_column("jockeys", "name", existing_type=sa.String(50), comment="騎手名", schema=SCHEMA)
    op.alter_column("jockeys", "jravan_code", existing_type=sa.String(10), comment="JRA-VAN騎手コード（5桁）", schema=SCHEMA)

    # --- trainers テーブル ---
    op.alter_column("trainers", "name", existing_type=sa.String(50), comment="調教師名", schema=SCHEMA)
    op.alter_column("trainers", "jravan_code", existing_type=sa.String(10), comment="JRA-VAN調教師コード（5桁）", schema=SCHEMA)

    # --- races テーブル ---
    op.alter_column("races", "date", existing_type=sa.String(8), comment="開催日（YYYYMMDD）", schema=SCHEMA)
    op.alter_column("races", "course", existing_type=sa.String(10), comment="競馬場コード（コード表2001: 01=札幌,05=東京,06=中山等）", schema=SCHEMA)
    op.alter_column("races", "course_name", existing_type=sa.String(20), comment="競馬場名（東京/中山/阪神等）", schema=SCHEMA)
    op.alter_column("races", "race_number", existing_type=sa.Integer(), comment="レース番号（1-12）", schema=SCHEMA)
    op.alter_column("races", "race_name", existing_type=sa.String(100), comment="レース名", schema=SCHEMA)
    op.alter_column("races", "surface", existing_type=sa.String(5), comment="トラック種別（芝/ダ/障）", schema=SCHEMA)
    op.alter_column("races", "distance", existing_type=sa.Integer(), comment="距離（m）", schema=SCHEMA)
    op.alter_column("races", "direction", existing_type=sa.String(5), comment="回り方向（右/左）", schema=SCHEMA)
    op.alter_column("races", "track_type", existing_type=sa.String(10), comment="トラック種類（内/外/直線）", schema=SCHEMA)
    op.alter_column("races", "condition", existing_type=sa.String(5), comment="馬場状態（良/稍/重/不）", schema=SCHEMA)
    op.alter_column("races", "weather", existing_type=sa.String(10), comment="天候（晴/曇/雨/小雨/雪/小雪）", schema=SCHEMA)
    op.alter_column("races", "grade", existing_type=sa.String(10), comment="グレード（G1/G2/G3/OP/条件等）", schema=SCHEMA)
    op.alter_column("races", "head_count", existing_type=sa.Integer(), comment="出走頭数", schema=SCHEMA)
    op.alter_column("races", "jravan_race_id", existing_type=sa.String(30), comment="JRA-VANレースID（16文字: year+month_day+course+kai+day+race_num）", schema=SCHEMA)
    op.alter_column("races", "created_at", existing_type=sa.DateTime(), comment="レコード作成日時", schema=SCHEMA)

    # --- race_entries テーブル ---
    op.alter_column("race_entries", "frame_number", existing_type=sa.Integer(), comment="枠番（1-8）", schema=SCHEMA)
    op.alter_column("race_entries", "horse_number", existing_type=sa.Integer(), comment="馬番（1-18）", schema=SCHEMA)
    op.alter_column("race_entries", "weight_carried", existing_type=sa.Numeric(4, 1), comment="負担重量（kg）", schema=SCHEMA)
    op.alter_column("race_entries", "horse_weight", existing_type=sa.Integer(), comment="馬体重（kg、計不明時はNone）", schema=SCHEMA)
    op.alter_column("race_entries", "weight_change", existing_type=sa.Integer(), comment="馬体重増減（kg、符号付き）", schema=SCHEMA)

    # --- race_results テーブル ---
    op.alter_column("race_results", "frame_number", existing_type=sa.Integer(), comment="枠番（1-8）", schema=SCHEMA)
    op.alter_column("race_results", "horse_number", existing_type=sa.Integer(), comment="馬番（1-18）", schema=SCHEMA)
    op.alter_column("race_results", "weight_carried", existing_type=sa.Numeric(4, 1), comment="負担重量（kg）", schema=SCHEMA)
    op.alter_column("race_results", "horse_weight", existing_type=sa.Integer(), comment="馬体重（kg）", schema=SCHEMA)
    op.alter_column("race_results", "weight_change", existing_type=sa.Integer(), comment="馬体重増減（kg、符号付き）", schema=SCHEMA)
    op.alter_column("race_results", "margin", existing_type=sa.Numeric(4, 1), comment="着差（馬身）", schema=SCHEMA)
    op.alter_column("race_results", "finish_position", existing_type=sa.Integer(), comment="確定着順", schema=SCHEMA)
    op.alter_column("race_results", "finish_time", existing_type=sa.Numeric(6, 1), comment="走破タイム（秒）", schema=SCHEMA)
    op.alter_column("race_results", "passing_1", existing_type=sa.Integer(), comment="1コーナー通過順位", schema=SCHEMA)
    op.alter_column("race_results", "passing_2", existing_type=sa.Integer(), comment="2コーナー通過順位", schema=SCHEMA)
    op.alter_column("race_results", "passing_3", existing_type=sa.Integer(), comment="3コーナー通過順位", schema=SCHEMA)
    op.alter_column("race_results", "passing_4", existing_type=sa.Integer(), comment="4コーナー通過順位", schema=SCHEMA)
    op.alter_column("race_results", "last_3f", existing_type=sa.Numeric(3, 1), comment="後3ハロンタイム（秒、個馬別）", schema=SCHEMA)
    op.alter_column("race_results", "abnormality_code", existing_type=sa.Integer(), comment="異常区分（1:出走取消,2:発走除外,3:競走中止,4:失格,5:降着）", schema=SCHEMA)
    op.alter_column("race_results", "created_at", existing_type=sa.DateTime(), comment="レコード作成日時", schema=SCHEMA)

    # --- track_conditions テーブル ---
    op.alter_column("track_conditions", "date", existing_type=sa.String(8), comment="開催日（YYYYMMDD）", schema=SCHEMA)
    op.alter_column("track_conditions", "course", existing_type=sa.String(10), comment="競馬場コード", schema=SCHEMA)
    op.alter_column("track_conditions", "surface", existing_type=sa.String(5), comment="トラック種別（芝/ダ）", schema=SCHEMA)
    op.alter_column("track_conditions", "distance", existing_type=sa.Integer(), comment="距離（m）", schema=SCHEMA)
    op.alter_column("track_conditions", "condition", existing_type=sa.String(5), comment="馬場状態（良/稍/重/不）", schema=SCHEMA)
    op.alter_column("track_conditions", "bias_value", existing_type=sa.Numeric(5, 2), comment="馬場差値（基準タイムとの差、秒）", schema=SCHEMA)

    # --- calculated_indices テーブル ---
    op.alter_column("calculated_indices", "version", existing_type=sa.Integer(), comment="算出バージョン（指数ロジック変更時にインクリメント）", schema=SCHEMA)
    op.alter_column("calculated_indices", "speed_index", existing_type=sa.Numeric(5, 1), comment="スピード指数（基準50、標準偏差10）", schema=SCHEMA)
    op.alter_column("calculated_indices", "adjusted_speed_index", existing_type=sa.Numeric(5, 1), comment="補正スピード指数（斤量・馬場差補正後）", schema=SCHEMA)
    op.alter_column("calculated_indices", "last_3f_index", existing_type=sa.Numeric(5, 1), comment="後3ハロン指数", schema=SCHEMA)
    op.alter_column("calculated_indices", "course_aptitude", existing_type=sa.Numeric(5, 1), comment="コース適性指数", schema=SCHEMA)
    op.alter_column("calculated_indices", "distance_aptitude", existing_type=sa.Numeric(5, 1), comment="距離適性指数", schema=SCHEMA)
    op.alter_column("calculated_indices", "position_advantage", existing_type=sa.Numeric(5, 1), comment="枠順有利不利指数", schema=SCHEMA)
    op.alter_column("calculated_indices", "jockey_index", existing_type=sa.Numeric(5, 1), comment="騎手指数", schema=SCHEMA)
    op.alter_column("calculated_indices", "trainer_index", existing_type=sa.Numeric(5, 1), comment="調教師指数", schema=SCHEMA)
    op.alter_column("calculated_indices", "pedigree_index", existing_type=sa.Numeric(5, 1), comment="血統指数", schema=SCHEMA)
    op.alter_column("calculated_indices", "pace_index", existing_type=sa.Numeric(5, 1), comment="展開指数", schema=SCHEMA)
    op.alter_column("calculated_indices", "rotation_index", existing_type=sa.Numeric(5, 1), comment="ローテーション指数", schema=SCHEMA)
    op.alter_column("calculated_indices", "training_index", existing_type=sa.Numeric(5, 1), comment="調教指数", schema=SCHEMA)
    op.alter_column("calculated_indices", "paddock_index", existing_type=sa.Numeric(5, 1), comment="パドック指数", schema=SCHEMA)
    op.alter_column("calculated_indices", "disadvantage_flag", existing_type=sa.Boolean(), comment="不利フラグ（True:レース中に不利あり）", schema=SCHEMA)
    op.alter_column("calculated_indices", "composite_index", existing_type=sa.Numeric(5, 1), comment="総合指数（全指数の加重平均）", schema=SCHEMA)
    op.alter_column("calculated_indices", "win_probability", existing_type=sa.Numeric(5, 4), comment="単勝確率", schema=SCHEMA)
    op.alter_column("calculated_indices", "place_probability", existing_type=sa.Numeric(5, 4), comment="複勝確率", schema=SCHEMA)
    op.alter_column("calculated_indices", "calculated_at", existing_type=sa.DateTime(), comment="指数算出日時", schema=SCHEMA)

    # --- entry_changes テーブル ---
    op.alter_column("entry_changes", "change_type", existing_type=sa.String(20), comment="変更種別（scratch/jockey_change/weight_change等）", schema=SCHEMA)
    op.alter_column("entry_changes", "old_value", existing_type=sa.String(100), comment="変更前の値", schema=SCHEMA)
    op.alter_column("entry_changes", "new_value", existing_type=sa.String(100), comment="変更後の値", schema=SCHEMA)
    op.alter_column("entry_changes", "detected_at", existing_type=sa.DateTime(), comment="変更検知日時", schema=SCHEMA)
    op.alter_column("entry_changes", "recalc_triggered", existing_type=sa.Boolean(), comment="再算出実行済みフラグ", schema=SCHEMA)

    # --- odds_history テーブル ---
    op.alter_column("odds_history", "bet_type", existing_type=sa.String(20), comment="賭式（win/place/quinella/trio/trifecta等）", schema=SCHEMA)
    op.alter_column("odds_history", "combination", existing_type=sa.String(50), comment="馬番組み合わせ（単: '3', 連: '3-7', 3連: '3-7-12'）", schema=SCHEMA)
    op.alter_column("odds_history", "odds", existing_type=sa.Numeric(10, 1), comment="オッズ（倍）", schema=SCHEMA)
    op.alter_column("odds_history", "fetched_at", existing_type=sa.DateTime(), comment="取得日時", schema=SCHEMA)


def downgrade() -> None:
    # --- odds_history テーブル ---
    op.alter_column("odds_history", "fetched_at", existing_type=sa.DateTime(), comment=None, schema=SCHEMA)
    op.alter_column("odds_history", "odds", existing_type=sa.Numeric(10, 1), comment=None, schema=SCHEMA)
    op.alter_column("odds_history", "combination", existing_type=sa.String(50), comment=None, schema=SCHEMA)
    op.alter_column("odds_history", "bet_type", existing_type=sa.String(20), comment=None, schema=SCHEMA)

    # --- entry_changes テーブル ---
    op.alter_column("entry_changes", "recalc_triggered", existing_type=sa.Boolean(), comment=None, schema=SCHEMA)
    op.alter_column("entry_changes", "detected_at", existing_type=sa.DateTime(), comment=None, schema=SCHEMA)
    op.alter_column("entry_changes", "new_value", existing_type=sa.String(100), comment=None, schema=SCHEMA)
    op.alter_column("entry_changes", "old_value", existing_type=sa.String(100), comment=None, schema=SCHEMA)
    op.alter_column("entry_changes", "change_type", existing_type=sa.String(20), comment=None, schema=SCHEMA)

    # --- calculated_indices テーブル ---
    op.alter_column("calculated_indices", "calculated_at", existing_type=sa.DateTime(), comment=None, schema=SCHEMA)
    op.alter_column("calculated_indices", "place_probability", existing_type=sa.Numeric(5, 4), comment=None, schema=SCHEMA)
    op.alter_column("calculated_indices", "win_probability", existing_type=sa.Numeric(5, 4), comment=None, schema=SCHEMA)
    op.alter_column("calculated_indices", "composite_index", existing_type=sa.Numeric(5, 1), comment=None, schema=SCHEMA)
    op.alter_column("calculated_indices", "disadvantage_flag", existing_type=sa.Boolean(), comment=None, schema=SCHEMA)
    op.alter_column("calculated_indices", "paddock_index", existing_type=sa.Numeric(5, 1), comment=None, schema=SCHEMA)
    op.alter_column("calculated_indices", "training_index", existing_type=sa.Numeric(5, 1), comment=None, schema=SCHEMA)
    op.alter_column("calculated_indices", "rotation_index", existing_type=sa.Numeric(5, 1), comment=None, schema=SCHEMA)
    op.alter_column("calculated_indices", "pace_index", existing_type=sa.Numeric(5, 1), comment=None, schema=SCHEMA)
    op.alter_column("calculated_indices", "pedigree_index", existing_type=sa.Numeric(5, 1), comment=None, schema=SCHEMA)
    op.alter_column("calculated_indices", "trainer_index", existing_type=sa.Numeric(5, 1), comment=None, schema=SCHEMA)
    op.alter_column("calculated_indices", "jockey_index", existing_type=sa.Numeric(5, 1), comment=None, schema=SCHEMA)
    op.alter_column("calculated_indices", "position_advantage", existing_type=sa.Numeric(5, 1), comment=None, schema=SCHEMA)
    op.alter_column("calculated_indices", "distance_aptitude", existing_type=sa.Numeric(5, 1), comment=None, schema=SCHEMA)
    op.alter_column("calculated_indices", "course_aptitude", existing_type=sa.Numeric(5, 1), comment=None, schema=SCHEMA)
    op.alter_column("calculated_indices", "last_3f_index", existing_type=sa.Numeric(5, 1), comment=None, schema=SCHEMA)
    op.alter_column("calculated_indices", "adjusted_speed_index", existing_type=sa.Numeric(5, 1), comment=None, schema=SCHEMA)
    op.alter_column("calculated_indices", "speed_index", existing_type=sa.Numeric(5, 1), comment=None, schema=SCHEMA)
    op.alter_column("calculated_indices", "version", existing_type=sa.Integer(), comment=None, schema=SCHEMA)

    # --- track_conditions テーブル ---
    op.alter_column("track_conditions", "bias_value", existing_type=sa.Numeric(5, 2), comment=None, schema=SCHEMA)
    op.alter_column("track_conditions", "condition", existing_type=sa.String(5), comment=None, schema=SCHEMA)
    op.alter_column("track_conditions", "distance", existing_type=sa.Integer(), comment=None, schema=SCHEMA)
    op.alter_column("track_conditions", "surface", existing_type=sa.String(5), comment=None, schema=SCHEMA)
    op.alter_column("track_conditions", "course", existing_type=sa.String(10), comment=None, schema=SCHEMA)
    op.alter_column("track_conditions", "date", existing_type=sa.String(8), comment=None, schema=SCHEMA)

    # --- race_results テーブル ---
    op.alter_column("race_results", "created_at", existing_type=sa.DateTime(), comment=None, schema=SCHEMA)
    op.alter_column("race_results", "abnormality_code", existing_type=sa.Integer(), comment=None, schema=SCHEMA)
    op.alter_column("race_results", "last_3f", existing_type=sa.Numeric(3, 1), comment=None, schema=SCHEMA)
    op.alter_column("race_results", "passing_4", existing_type=sa.Integer(), comment=None, schema=SCHEMA)
    op.alter_column("race_results", "passing_3", existing_type=sa.Integer(), comment=None, schema=SCHEMA)
    op.alter_column("race_results", "passing_2", existing_type=sa.Integer(), comment=None, schema=SCHEMA)
    op.alter_column("race_results", "passing_1", existing_type=sa.Integer(), comment=None, schema=SCHEMA)
    op.alter_column("race_results", "finish_time", existing_type=sa.Numeric(6, 1), comment=None, schema=SCHEMA)
    op.alter_column("race_results", "finish_position", existing_type=sa.Integer(), comment=None, schema=SCHEMA)
    op.alter_column("race_results", "margin", existing_type=sa.Numeric(4, 1), comment=None, schema=SCHEMA)
    op.alter_column("race_results", "weight_change", existing_type=sa.Integer(), comment=None, schema=SCHEMA)
    op.alter_column("race_results", "horse_weight", existing_type=sa.Integer(), comment=None, schema=SCHEMA)
    op.alter_column("race_results", "weight_carried", existing_type=sa.Numeric(4, 1), comment=None, schema=SCHEMA)
    op.alter_column("race_results", "horse_number", existing_type=sa.Integer(), comment=None, schema=SCHEMA)
    op.alter_column("race_results", "frame_number", existing_type=sa.Integer(), comment=None, schema=SCHEMA)

    # --- race_entries テーブル ---
    op.alter_column("race_entries", "weight_change", existing_type=sa.Integer(), comment=None, schema=SCHEMA)
    op.alter_column("race_entries", "horse_weight", existing_type=sa.Integer(), comment=None, schema=SCHEMA)
    op.alter_column("race_entries", "weight_carried", existing_type=sa.Numeric(4, 1), comment=None, schema=SCHEMA)
    op.alter_column("race_entries", "horse_number", existing_type=sa.Integer(), comment=None, schema=SCHEMA)
    op.alter_column("race_entries", "frame_number", existing_type=sa.Integer(), comment=None, schema=SCHEMA)

    # --- races テーブル ---
    op.alter_column("races", "created_at", existing_type=sa.DateTime(), comment=None, schema=SCHEMA)
    op.alter_column("races", "jravan_race_id", existing_type=sa.String(30), comment=None, schema=SCHEMA)
    op.alter_column("races", "head_count", existing_type=sa.Integer(), comment=None, schema=SCHEMA)
    op.alter_column("races", "grade", existing_type=sa.String(10), comment=None, schema=SCHEMA)
    op.alter_column("races", "weather", existing_type=sa.String(10), comment=None, schema=SCHEMA)
    op.alter_column("races", "condition", existing_type=sa.String(5), comment=None, schema=SCHEMA)
    op.alter_column("races", "track_type", existing_type=sa.String(10), comment=None, schema=SCHEMA)
    op.alter_column("races", "direction", existing_type=sa.String(5), comment=None, schema=SCHEMA)
    op.alter_column("races", "distance", existing_type=sa.Integer(), comment=None, schema=SCHEMA)
    op.alter_column("races", "surface", existing_type=sa.String(5), comment=None, schema=SCHEMA)
    op.alter_column("races", "race_name", existing_type=sa.String(100), comment=None, schema=SCHEMA)
    op.alter_column("races", "race_number", existing_type=sa.Integer(), comment=None, schema=SCHEMA)
    op.alter_column("races", "course_name", existing_type=sa.String(20), comment=None, schema=SCHEMA)
    op.alter_column("races", "course", existing_type=sa.String(10), comment=None, schema=SCHEMA)
    op.alter_column("races", "date", existing_type=sa.String(8), comment=None, schema=SCHEMA)

    # --- trainers テーブル ---
    op.alter_column("trainers", "jravan_code", existing_type=sa.String(10), comment=None, schema=SCHEMA)
    op.alter_column("trainers", "name", existing_type=sa.String(50), comment=None, schema=SCHEMA)

    # --- jockeys テーブル ---
    op.alter_column("jockeys", "jravan_code", existing_type=sa.String(10), comment=None, schema=SCHEMA)
    op.alter_column("jockeys", "name", existing_type=sa.String(50), comment=None, schema=SCHEMA)

    # --- pedigrees テーブル ---
    op.alter_column("pedigrees", "dam_sire_line", existing_type=sa.String(50), comment=None, schema=SCHEMA)
    op.alter_column("pedigrees", "sire_line", existing_type=sa.String(50), comment=None, schema=SCHEMA)
    op.alter_column("pedigrees", "sire_of_dam", existing_type=sa.String(100), comment=None, schema=SCHEMA)
    op.alter_column("pedigrees", "dam", existing_type=sa.String(100), comment=None, schema=SCHEMA)
    op.alter_column("pedigrees", "sire", existing_type=sa.String(100), comment=None, schema=SCHEMA)

    # --- horses テーブル ---
    op.alter_column("horses", "created_at", existing_type=sa.DateTime(), comment=None, schema=SCHEMA)
    op.alter_column("horses", "jravan_code", existing_type=sa.String(20), comment=None, schema=SCHEMA)
    op.alter_column("horses", "breeder", existing_type=sa.String(100), comment=None, schema=SCHEMA)
    op.alter_column("horses", "owner", existing_type=sa.String(100), comment=None, schema=SCHEMA)
    op.alter_column("horses", "coat_color", existing_type=sa.String(20), comment=None, schema=SCHEMA)
    op.alter_column("horses", "birthday", existing_type=sa.String(8), comment=None, schema=SCHEMA)
    op.alter_column("horses", "sex", existing_type=sa.String(10), comment=None, schema=SCHEMA)
    op.alter_column("horses", "name", existing_type=sa.String(100), comment=None, schema=SCHEMA)
