"""レース・出馬表・成績インポーター

RA/SEレコードをパースしてVPS PostgreSQL（keibaスキーマ）へUPSERTする。
重複実行に対して冪等（同一jravan_*_idが存在する場合は更新）。
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..db.models import Horse, Jockey, Race, RaceEntry, RaceResult, Trainer
from .jvlink_parser import parse_ra, parse_se

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# 単位変換ヘルパー
# -------------------------------------------------------------------
def _finish_time_to_decimal(raw: int | None) -> Decimal | None:
    """SEレコードのタイムフィールド（0.1秒単位整数）をDecimal秒に変換する。

    JRA-VAN SEレコードのタイムは0.1秒単位の整数で格納されている。
    例: 934 → 93.4秒, 1440 → 144.0秒

    Args:
        raw: パーサーが抽出した整数値（0.1秒単位）

    Returns:
        秒単位のDecimal（例: Decimal('93.4')）
    """
    if raw is None or raw <= 0:
        return None
    return Decimal(str(round(raw / 10, 1)))


def _last3f_to_decimal(raw: int | None) -> Decimal | None:
    """上がり3Fフィールド（0.1秒単位整数）をDecimal秒に変換する。

    例: 336 → 33.6秒

    Args:
        raw: パーサーが抽出した整数値（0.1秒単位）

    Returns:
        秒単位のDecimal（例: Decimal('33.6')）
    """
    if raw is None or raw <= 0:
        return None
    return Decimal(str(round(raw / 10, 1)))


# 場コード → race.date用の年変換ヘルパー
# RA/SE の年フィールドは4桁（例: "2026"）
def _year4(year_str: str) -> str:
    """2桁または4桁の年文字列を4桁に正規化する。"""
    if len(year_str) == 2:
        return f"20{year_str}"
    return year_str[:4]


class RaceImporter:
    """RA/SEレコードをDBに取り込むクラス。"""

    def __init__(self, db: Session) -> None:
        """初期化。

        Args:
            db: SQLAlchemyセッション
        """
        self.db = db

    # ------------------------------------------------------------------
    # 公開メソッド
    # ------------------------------------------------------------------
    def import_records(self, records: list[dict[str, str]]) -> dict[str, int]:
        """RA/SEレコードのリストをDBへ取り込む。

        Args:
            records: [{"rec_id": "RA", "data": "RA1..."}, ...]

        Returns:
            {"races": N, "entries": N, "results": N, "errors": N}
        """
        stats = {"races": 0, "entries": 0, "results": 0, "errors": 0}

        for rec in records:
            rec_id = rec.get("rec_id", "")
            try:
                if rec_id == "RA":
                    parsed = parse_ra(rec["data"])
                    if parsed:
                        self._upsert_race(parsed)
                        stats["races"] += 1
                elif rec_id == "SE":
                    parsed = parse_se(rec["data"])
                    if parsed:
                        self._upsert_entry_and_result(parsed)
                        if parsed.get("finish_position"):
                            stats["results"] += 1
                        else:
                            stats["entries"] += 1
            except Exception as e:
                logger.error(f"Import error rec_id={rec_id}: {e}")
                stats["errors"] += 1

        self.db.flush()
        return stats

    # ------------------------------------------------------------------
    # 内部メソッド
    # ------------------------------------------------------------------
    def _upsert_race(self, parsed: dict[str, Any]) -> Race:
        """Raceを upsert する（jravan_race_id でユニーク識別）。"""
        race_id_key = parsed["jravan_race_id"]

        # 実際の開催日: パーサーが year + month_day から組み立てた "YYYYMMDD" を使用
        race_date = parsed.get("race_date", "")

        stmt = (
            insert(Race)
            .values(
                jravan_race_id=race_id_key,
                date=race_date,
                course=parsed.get("course", ""),
                course_name=parsed.get("course_name", ""),
                race_number=parsed.get("race_number", 0),
                race_name=parsed.get("race_name") or None,
                surface=parsed.get("surface", ""),
                distance=parsed.get("distance") or 0,
                direction=parsed.get("direction"),
                condition=parsed.get("condition"),
                weather=parsed.get("weather"),
                grade=parsed.get("grade") or None,
            )
            .on_conflict_do_update(
                index_elements=["jravan_race_id"],
                set_={
                    "race_name": parsed.get("race_name") or None,
                    "surface": parsed.get("surface", ""),
                    "distance": parsed.get("distance") or 0,
                    "direction": parsed.get("direction"),
                    "condition": parsed.get("condition"),
                    "weather": parsed.get("weather"),
                    "grade": parsed.get("grade") or None,
                },
            )
            .returning(Race.id)
        )
        result = self.db.execute(stmt)
        race_db_id = result.scalar_one()
        return race_db_id

    def _get_or_create_horse(self, parsed: dict[str, Any]) -> int:
        """馬をget_or_createする。jravan_codeで識別。"""
        code = parsed.get("jravan_horse_code", "")
        horse = (
            self.db.query(Horse)
            .filter(Horse.jravan_code == code)
            .first()
        )
        if horse:
            return horse.id

        horse = Horse(
            name=parsed.get("horse_name", ""),
            sex=parsed.get("sex", ""),
            birthday="",  # SEレコードには誕生日がないため空
            jravan_code=code,
        )
        self.db.add(horse)
        self.db.flush()
        return horse.id

    def _get_or_create_jockey(self, code: str, name: str) -> int | None:
        """騎手をget_or_createする。"""
        if not code:
            return None
        jockey = self.db.query(Jockey).filter(Jockey.jravan_code == code).first()
        if jockey:
            return jockey.id
        jockey = Jockey(name=name, jravan_code=code)
        self.db.add(jockey)
        self.db.flush()
        return jockey.id

    def _get_or_create_trainer(self, code: str, name: str) -> int | None:
        """調教師をget_or_createする。"""
        if not code:
            return None
        trainer = self.db.query(Trainer).filter(Trainer.jravan_code == code).first()
        if trainer:
            return trainer.id
        trainer = Trainer(name=name, jravan_code=code)
        self.db.add(trainer)
        self.db.flush()
        return trainer.id

    def _get_race_id_by_jravan(self, jravan_race_id: str) -> int | None:
        """jravan_race_id からDBのRace.idを取得する。"""
        race = (
            self.db.query(Race.id)
            .filter(Race.jravan_race_id == jravan_race_id)
            .scalar()
        )
        return race

    def _upsert_entry_and_result(self, parsed: dict[str, Any]) -> None:
        """SEレコードからRaceEntry/RaceResultをupsertする。"""
        race_db_id = self._get_race_id_by_jravan(parsed["jravan_race_id"])
        if race_db_id is None:
            # Raceが先にインポートされていない場合はスキップ
            # （バッチ内でRAが先に来ることを想定。後処理でリトライ可能）
            logger.warning(f"Race not found for jravan_race_id={parsed['jravan_race_id']}, SE skipped")
            return

        horse_id = self._get_or_create_horse(parsed)
        jockey_id = self._get_or_create_jockey(
            parsed.get("jravan_jockey_code", ""),
            parsed.get("jockey_name", ""),
        )
        trainer_id = self._get_or_create_trainer(
            parsed.get("jravan_trainer_code", ""),
            parsed.get("trainer_name", ""),
        )

        # RaceEntry: 出馬表として upsert（race_id + horse_number でユニーク）
        entry_stmt = (
            insert(RaceEntry)
            .values(
                race_id=race_db_id,
                horse_id=horse_id,
                frame_number=parsed.get("frame_number") or 0,
                horse_number=parsed.get("horse_number") or 0,
                jockey_id=jockey_id,
                trainer_id=trainer_id,
                weight_carried=parsed.get("weight_carried"),
                horse_weight=parsed.get("horse_weight"),
                weight_change=parsed.get("weight_change"),
            )
            .on_conflict_do_update(
                index_elements=["race_id", "horse_number"],
                set_={
                    "jockey_id": jockey_id,
                    "trainer_id": trainer_id,
                    "weight_carried": parsed.get("weight_carried"),
                    "horse_weight": parsed.get("horse_weight"),
                    "weight_change": parsed.get("weight_change"),
                },
            )
            .returning(RaceEntry.id)
        )
        entry_result = self.db.execute(entry_stmt)
        entry_id = entry_result.scalar_one()

        # 成績データがある場合（着順 > 0 または 異常区分 > 0）はRaceResultも upsert
        finish_pos = parsed.get("finish_position")
        abnormal = parsed.get("abnormality_code", 0)
        if finish_pos or abnormal:
            finish_time_raw = parsed.get("finish_time")
            last_3f_raw = parsed.get("last_3f")

            result_stmt = (
                insert(RaceResult)
                .values(
                    race_id=race_db_id,
                    horse_id=horse_id,
                    entry_id=entry_id,
                    finish_position=finish_pos,
                    frame_number=parsed.get("frame_number"),
                    horse_number=parsed.get("horse_number"),
                    jockey_id=jockey_id,
                    weight_carried=parsed.get("weight_carried"),
                    horse_weight=parsed.get("horse_weight"),
                    weight_change=parsed.get("weight_change"),
                    # 0.1秒単位の整数を秒単位Decimalへ変換（例: 934 → 93.4秒）
                    finish_time=_finish_time_to_decimal(finish_time_raw),
                    # 0.1秒単位の整数を秒単位Decimalへ変換（例: 336 → 33.6秒）
                    last_3f=_last3f_to_decimal(last_3f_raw),
                    passing_1=parsed.get("passing_1"),
                    passing_2=parsed.get("passing_2"),
                    passing_3=parsed.get("passing_3"),
                    passing_4=parsed.get("passing_4"),
                    abnormality_code=abnormal,
                )
                .on_conflict_do_update(
                    index_elements=["race_id", "horse_id"],
                    set_={
                        "finish_position": finish_pos,
                        "finish_time": _finish_time_to_decimal(finish_time_raw),
                        "last_3f": _last3f_to_decimal(last_3f_raw),
                        "passing_1": parsed.get("passing_1"),
                        "passing_2": parsed.get("passing_2"),
                        "passing_3": parsed.get("passing_3"),
                        "passing_4": parsed.get("passing_4"),
                        "abnormality_code": abnormal,
                        "jockey_id": jockey_id,
                    },
                )
            )
            self.db.execute(result_stmt)
