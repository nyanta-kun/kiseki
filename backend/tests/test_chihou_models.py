"""地方競馬モデル定義 ユニットテスト

DB接続不要。SQLAlchemy モデルのスキーマ定義・制約を確認する。
"""

from __future__ import annotations

from sqlalchemy import UniqueConstraint

from src.db.chihou_models import (
    CHIHOU_SCHEMA,
    ChihouHorse,
    ChihouPedigree,
    ChihouRace,
    ChihouRaceEntry,
    ChihouRacePayout,
    ChihouRaceResult,
)


class TestChihouModels:
    """chihou スキーマのモデル定義テスト。"""

    # ------------------------------------------------------------------
    # スキーマ定数・テーブル名
    # ------------------------------------------------------------------

    def test_chihou_schema_constant(self) -> None:
        assert CHIHOU_SCHEMA == "chihou"

    def test_horse_table_name(self) -> None:
        assert ChihouHorse.__tablename__ == "horses"
        assert ChihouHorse.__table__.schema == "chihou"

    # ------------------------------------------------------------------
    # カラム存在確認
    # ------------------------------------------------------------------

    def test_race_has_umaconn_race_id(self) -> None:
        column_names = {c.name for c in ChihouRace.__table__.columns}
        assert "umaconn_race_id" in column_names

    def test_horse_has_umaconn_code(self) -> None:
        column_names = {c.name for c in ChihouHorse.__table__.columns}
        assert "umaconn_code" in column_names

    # ------------------------------------------------------------------
    # ユニーク制約
    # ------------------------------------------------------------------

    def test_race_entry_unique_constraint(self) -> None:
        constraint_names = {
            c.name
            for c in ChihouRaceEntry.__table__.constraints
            if isinstance(c, UniqueConstraint)
        }
        assert "uq_chihou_race_entry_horse_num" in constraint_names

    def test_race_result_unique_constraint(self) -> None:
        constraint_names = {
            c.name
            for c in ChihouRaceResult.__table__.constraints
            if isinstance(c, UniqueConstraint)
        }
        assert "uq_chihou_race_result_horse" in constraint_names

    def test_race_payout_unique_constraint(self) -> None:
        constraint_names = {
            c.name
            for c in ChihouRacePayout.__table__.constraints
            if isinstance(c, UniqueConstraint)
        }
        assert "uq_chihou_race_payouts_race_type_combo" in constraint_names

    def test_pedigree_unique_constraint(self) -> None:
        constraint_names = {
            c.name
            for c in ChihouPedigree.__table__.constraints
            if isinstance(c, UniqueConstraint)
        }
        assert "uq_chihou_pedigree_horse_id" in constraint_names

    # ------------------------------------------------------------------
    # jravan_ フィールドが存在しないこと
    # ------------------------------------------------------------------

    def test_no_jravan_fields(self) -> None:
        column_names = {c.name for c in ChihouRace.__table__.columns}
        assert not any("jravan" in name for name in column_names)

    # ------------------------------------------------------------------
    # 外部キーが chihou スキーマを参照すること
    # ------------------------------------------------------------------

    def test_foreign_keys_within_chihou_schema(self) -> None:
        """ChihouRaceEntry.race_id の FK 参照先が chihou.races であること。"""
        race_id_col = ChihouRaceEntry.__table__.columns["race_id"]
        fk_targets = {fk.target_fullname for fk in race_id_col.foreign_keys}
        assert "chihou.races.id" in fk_targets
