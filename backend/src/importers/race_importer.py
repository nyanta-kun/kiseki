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
        # リクエスト内キャッシュ（jravan_code → DB id）
        self._horse_cache: dict[str, int] = {}
        self._jockey_cache: dict[str, int] = {}
        self._trainer_cache: dict[str, int] = {}
        self._race_cache: dict[str, int] = {}

    # ------------------------------------------------------------------
    # 公開メソッド
    # ------------------------------------------------------------------
    def import_records(self, records: list[dict[str, str]]) -> dict[str, int]:
        """RA/SEレコードのリストをDBへ取り込む（バルクupsert処理）。

        Phase1: 全レコードをパースしてRA/SEを分離
        Phase2: バルクSQL（1ステートメントでN件）でupsert

        Args:
            records: [{"rec_id": "RA", "data": "RA1..."}, ...]

        Returns:
            {"races": N, "entries": N, "results": N, "errors": N}
        """
        stats = {"races": 0, "entries": 0, "results": 0, "errors": 0}

        # Phase1: パース
        ra_parsed: list[dict[str, Any]] = []
        se_parsed: list[dict[str, Any]] = []
        for rec in records:
            rec_id = rec.get("rec_id", "")
            try:
                if rec_id == "RA":
                    p = parse_ra(rec["data"])
                    if p:
                        ra_parsed.append(p)
                elif rec_id == "SE":
                    p = parse_se(rec["data"])
                    if p:
                        se_parsed.append(p)
            except Exception as e:
                logger.error(f"Parse error rec_id={rec_id}: {e}")
                stats["errors"] += 1

        # Phase2a: RAを1SQLでバルクupsert → raceキャッシュを構築
        if ra_parsed:
            try:
                self._bulk_upsert_races(ra_parsed)
                stats["races"] = len(ra_parsed)
            except Exception as e:
                logger.error(f"Bulk race upsert error: {e}")
                stats["errors"] += len(ra_parsed)

        if se_parsed:
            # Phase2b: 既存エンティティをバルクSELECTでキャッシュ構築（4 SQL）
            self._warm_up_caches(se_parsed)

            # Phase2c: 新規エンティティをバルクINSERT DO NOTHINGで登録（最大6 SQL）
            try:
                self._ensure_entities_bulk(se_parsed)
            except Exception as e:
                logger.error(f"Bulk entity create error: {e}")

            # Phase2d: entriesを1SQLでバルクupsert（RETURNING でentry_id取得）
            entry_map: dict[tuple[int, int], int] = {}
            try:
                entry_map = self._bulk_upsert_entries(se_parsed)
                stats["entries"] = len(entry_map)
            except Exception as e:
                logger.error(f"Bulk entry upsert error: {e}")
                stats["errors"] += len(se_parsed)

            # Phase2e: resultsを1SQLでバルクupsert
            if entry_map:
                try:
                    stats["results"] = self._bulk_upsert_results(se_parsed, entry_map)
                except Exception as e:
                    logger.error(f"Bulk result upsert error: {e}")
                    stats["errors"] += len(se_parsed)

        self.db.flush()
        return stats

    def _warm_up_caches(self, se_list: list[dict[str, Any]]) -> None:
        """SE群から必要なコードをバルクSELECTしてキャッシュを構築する。

        VPSへのRTTを最小化するため、N件のSELECTを各1件にまとめる。
        """
        horse_codes = {p["jravan_horse_code"] for p in se_list if p.get("jravan_horse_code")}
        jockey_codes = {p["jravan_jockey_code"] for p in se_list if p.get("jravan_jockey_code")}
        trainer_codes = {p["jravan_trainer_code"] for p in se_list if p.get("jravan_trainer_code")}
        race_ids = {p["jravan_race_id"] for p in se_list if p.get("jravan_race_id")}

        # 未キャッシュのコードのみSELECT
        new_horse_codes = horse_codes - set(self._horse_cache)
        new_jockey_codes = jockey_codes - set(self._jockey_cache)
        new_trainer_codes = trainer_codes - set(self._trainer_cache)
        new_race_ids = race_ids - set(self._race_cache)

        if new_horse_codes:
            rows = self.db.query(Horse.jravan_code, Horse.id).filter(
                Horse.jravan_code.in_(new_horse_codes)
            ).all()
            self._horse_cache.update({r.jravan_code: r.id for r in rows})

        if new_jockey_codes:
            rows = self.db.query(Jockey.jravan_code, Jockey.id).filter(
                Jockey.jravan_code.in_(new_jockey_codes)
            ).all()
            self._jockey_cache.update({r.jravan_code: r.id for r in rows})

        if new_trainer_codes:
            rows = self.db.query(Trainer.jravan_code, Trainer.id).filter(
                Trainer.jravan_code.in_(new_trainer_codes)
            ).all()
            self._trainer_cache.update({r.jravan_code: r.id for r in rows})

        if new_race_ids:
            rows = self.db.query(Race.jravan_race_id, Race.id).filter(
                Race.jravan_race_id.in_(new_race_ids)
            ).all()
            self._race_cache.update({r.jravan_race_id: r.id for r in rows})

    # ------------------------------------------------------------------
    # バルクupsertメソッド（N件を1 SQLで処理）
    # ------------------------------------------------------------------
    def _bulk_upsert_races(self, ra_list: list[dict[str, Any]]) -> None:
        """RAレコードを1 SQLでバルクupsertし、_race_cacheを更新する。

        個別ループ（N SQL）→ 1 SQL に変換することでVPS RTTを大幅削減。
        """
        values = [
            {
                "jravan_race_id": p["jravan_race_id"],
                "date": p.get("race_date", ""),
                "course": p.get("course", ""),
                "course_name": p.get("course_name", ""),
                "race_number": p.get("race_number", 0),
                "race_name": p.get("race_name") or None,
                "surface": p.get("surface", ""),
                "distance": p.get("distance") or 0,
                "direction": p.get("direction"),
                "condition": p.get("condition"),
                "weather": p.get("weather"),
                "grade": p.get("grade") or None,
                "post_time": p.get("post_time"),
                "race_type_code": p.get("race_type_code"),
                "weight_type_code": p.get("weight_type_code"),
                "prize_1st": p.get("prize_1st"),
                "prize_2nd": p.get("prize_2nd"),
                "prize_3rd": p.get("prize_3rd"),
                "head_count": p.get("head_count"),
                "registered_count": p.get("registered_count"),
                "finishers_count": p.get("finishers_count"),
                "first_3f": p.get("first_3f"),
                "last_3f_race": p.get("last_3f_race"),
                "lap_times": p.get("lap_times"),
                "record_update_type": p.get("record_update_type"),
                "prev_distance": p.get("prev_distance"),
                "prev_track_code": p.get("prev_track_code"),
                "prev_grade_code": p.get("prev_grade_code"),
                "prev_post_time": p.get("prev_post_time"),
            }
            for p in ra_list
        ]
        update_cols = [
            "race_name", "surface", "distance", "direction", "condition", "weather",
            "grade", "post_time", "race_type_code", "weight_type_code",
            "head_count", "prize_1st", "prize_2nd", "prize_3rd", "registered_count", "finishers_count",
            "first_3f", "last_3f_race", "lap_times", "record_update_type",
            "prev_distance", "prev_track_code", "prev_grade_code", "prev_post_time",
        ]
        stmt = insert(Race).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["jravan_race_id"],
            set_={col: stmt.excluded[col] for col in update_cols},
        ).returning(Race.id, Race.jravan_race_id)
        for race_id, jravan_id in self.db.execute(stmt):
            self._race_cache[jravan_id] = race_id

    def _ensure_entities_bulk(self, se_list: list[dict[str, Any]]) -> None:
        """SE群の新規馬・騎手・調教師をバルクINSERTしてキャッシュを補完する。

        _warm_up_cachesでDBに存在するものはキャッシュ済み。
        未キャッシュ（新規）のみ INSERT ON CONFLICT DO NOTHING → SELECT で取得。
        各エンティティ種別最大2 SQL（INSERT + SELECT）で完結する。
        """
        # --- 馬 ---
        new_horses: dict[str, dict[str, Any]] = {}
        for p in se_list:
            code = p.get("jravan_horse_code", "")
            if code and code not in self._horse_cache and code not in new_horses:
                new_horses[code] = {
                    "name": p.get("horse_name", ""),
                    "sex": p.get("sex", ""),
                    "birthday": "",
                    "jravan_code": code,
                }
        if new_horses:
            self.db.execute(
                insert(Horse).values(list(new_horses.values())).on_conflict_do_nothing()
            )
            rows = self.db.query(Horse.jravan_code, Horse.id).filter(
                Horse.jravan_code.in_(new_horses.keys())
            ).all()
            self._horse_cache.update({r.jravan_code: r.id for r in rows})

        # --- 騎手 ---
        new_jockeys: dict[str, dict[str, Any]] = {}
        for p in se_list:
            code = p.get("jravan_jockey_code", "")
            if code and code not in self._jockey_cache and code not in new_jockeys:
                new_jockeys[code] = {"name": p.get("jockey_name", ""), "jravan_code": code}
        if new_jockeys:
            self.db.execute(
                insert(Jockey).values(list(new_jockeys.values())).on_conflict_do_nothing()
            )
            rows = self.db.query(Jockey.jravan_code, Jockey.id).filter(
                Jockey.jravan_code.in_(new_jockeys.keys())
            ).all()
            self._jockey_cache.update({r.jravan_code: r.id for r in rows})

        # --- 調教師 ---
        new_trainers: dict[str, dict[str, Any]] = {}
        for p in se_list:
            code = p.get("jravan_trainer_code", "")
            if code and code not in self._trainer_cache and code not in new_trainers:
                new_trainers[code] = {"name": p.get("trainer_name", ""), "jravan_code": code}
        if new_trainers:
            self.db.execute(
                insert(Trainer).values(list(new_trainers.values())).on_conflict_do_nothing()
            )
            rows = self.db.query(Trainer.jravan_code, Trainer.id).filter(
                Trainer.jravan_code.in_(new_trainers.keys())
            ).all()
            self._trainer_cache.update({r.jravan_code: r.id for r in rows})

    def _bulk_upsert_entries(
        self, se_list: list[dict[str, Any]]
    ) -> dict[tuple[int, int], int]:
        """SEレコードのRaceEntryを1 SQLでバルクupsertする。

        Returns:
            {(race_id, horse_number): entry_id} — result upsertで使用するマップ
        """
        values = []
        for p in se_list:
            race_id = self._race_cache.get(p.get("jravan_race_id", ""))
            horse_id = self._horse_cache.get(p.get("jravan_horse_code", ""))
            if not race_id or not horse_id:
                logger.warning(
                    f"Entry skip: race={p.get('jravan_race_id')} horse={p.get('jravan_horse_code')} not in cache"
                )
                continue
            values.append({
                "race_id": race_id,
                "horse_id": horse_id,
                "frame_number": p.get("frame_number") or 0,
                "horse_number": p.get("horse_number") or 0,
                "jockey_id": self._jockey_cache.get(p.get("jravan_jockey_code", "")),
                "trainer_id": self._trainer_cache.get(p.get("jravan_trainer_code", "")),
                "weight_carried": p.get("weight_carried"),
                "horse_weight": p.get("horse_weight"),
                "weight_change": p.get("weight_change"),
                "horse_age": p.get("horse_age"),
                "east_west_code": p.get("east_west_code"),
                "prev_weight_carried": p.get("prev_weight_carried"),
                "blinker": p.get("blinker"),
                "prev_jockey_code": p.get("prev_jockey_code"),
                "jockey_apprentice_code": p.get("jockey_apprentice_code"),
            })
        if not values:
            return {}
        update_cols = [
            "jockey_id", "trainer_id", "weight_carried", "horse_weight", "weight_change",
            "horse_age", "east_west_code", "prev_weight_carried", "blinker",
            "prev_jockey_code", "jockey_apprentice_code",
        ]
        stmt = insert(RaceEntry).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["race_id", "horse_number"],
            set_={col: stmt.excluded[col] for col in update_cols},
        ).returning(RaceEntry.id, RaceEntry.race_id, RaceEntry.horse_number)
        entry_map: dict[tuple[int, int], int] = {}
        for entry_id, race_id, horse_num in self.db.execute(stmt):
            entry_map[(race_id, horse_num)] = entry_id
        return entry_map

    def _bulk_upsert_results(
        self, se_list: list[dict[str, Any]], entry_map: dict[tuple[int, int], int]
    ) -> int:
        """SEレコードのRaceResultを1 SQLでバルクupsertする。

        Args:
            entry_map: _bulk_upsert_entries()の戻り値

        Returns:
            upsertした件数
        """
        values = []
        for p in se_list:
            finish_pos = p.get("finish_position")
            abnormal = p.get("abnormality_code", 0)
            if not finish_pos and not abnormal:
                continue
            race_id = self._race_cache.get(p.get("jravan_race_id", ""))
            horse_id = self._horse_cache.get(p.get("jravan_horse_code", ""))
            horse_num = p.get("horse_number") or 0
            if not race_id or not horse_id:
                continue
            entry_id = entry_map.get((race_id, horse_num))
            if not entry_id:
                continue
            finish_time_raw = p.get("finish_time")
            last_3f_raw = p.get("last_3f")
            values.append({
                "race_id": race_id,
                "horse_id": horse_id,
                "entry_id": entry_id,
                "finish_position": finish_pos,
                "frame_number": p.get("frame_number"),
                "horse_number": horse_num,
                "jockey_id": self._jockey_cache.get(p.get("jravan_jockey_code", "")),
                "weight_carried": p.get("weight_carried"),
                "horse_weight": p.get("horse_weight"),
                "weight_change": p.get("weight_change"),
                "finish_time": _finish_time_to_decimal(finish_time_raw),
                "last_3f": _last3f_to_decimal(last_3f_raw),
                "passing_1": p.get("passing_1"),
                "passing_2": p.get("passing_2"),
                "passing_3": p.get("passing_3"),
                "passing_4": p.get("passing_4"),
                "abnormality_code": abnormal,
                "arrival_position": p.get("arrival_position"),
                "dead_heat": p.get("dead_heat"),
                "margin_code": p.get("margin_code"),
                "win_odds": p.get("win_odds"),
                "win_popularity": p.get("win_popularity"),
                "prize_money": p.get("prize_money"),
                "last_4f": p.get("last_4f"),
                "time_diff": p.get("time_diff"),
                "running_style": p.get("running_style"),
            })
        if not values:
            return 0
        update_cols = [
            "finish_position", "finish_time", "last_3f",
            "passing_1", "passing_2", "passing_3", "passing_4",
            "abnormality_code", "jockey_id", "arrival_position", "dead_heat",
            "margin_code", "win_odds", "win_popularity", "prize_money",
            "last_4f", "time_diff", "running_style",
        ]
        stmt = insert(RaceResult).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["race_id", "horse_id"],
            set_={col: stmt.excluded[col] for col in update_cols},
        )
        self.db.execute(stmt)
        return len(values)

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
                post_time=parsed.get("post_time"),
                race_type_code=parsed.get("race_type_code"),
                weight_type_code=parsed.get("weight_type_code"),
                prize_1st=parsed.get("prize_1st"),
                prize_2nd=parsed.get("prize_2nd"),
                prize_3rd=parsed.get("prize_3rd"),
                registered_count=parsed.get("registered_count"),
                finishers_count=parsed.get("finishers_count"),
                first_3f=parsed.get("first_3f"),
                last_3f_race=parsed.get("last_3f_race"),
                lap_times=parsed.get("lap_times"),
                record_update_type=parsed.get("record_update_type"),
                prev_distance=parsed.get("prev_distance"),
                prev_track_code=parsed.get("prev_track_code"),
                prev_grade_code=parsed.get("prev_grade_code"),
                prev_post_time=parsed.get("prev_post_time"),
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
                    "post_time": parsed.get("post_time"),
                    "race_type_code": parsed.get("race_type_code"),
                    "weight_type_code": parsed.get("weight_type_code"),
                    "prize_1st": parsed.get("prize_1st"),
                    "prize_2nd": parsed.get("prize_2nd"),
                    "prize_3rd": parsed.get("prize_3rd"),
                    "registered_count": parsed.get("registered_count"),
                    "finishers_count": parsed.get("finishers_count"),
                    "first_3f": parsed.get("first_3f"),
                    "last_3f_race": parsed.get("last_3f_race"),
                    "lap_times": parsed.get("lap_times"),
                    "record_update_type": parsed.get("record_update_type"),
                    "prev_distance": parsed.get("prev_distance"),
                    "prev_track_code": parsed.get("prev_track_code"),
                    "prev_grade_code": parsed.get("prev_grade_code"),
                    "prev_post_time": parsed.get("prev_post_time"),
                },
            )
            .returning(Race.id)
        )
        result = self.db.execute(stmt)
        race_db_id = result.scalar_one()
        return race_db_id

    def _get_or_create_horse(self, parsed: dict[str, Any]) -> int:
        """馬をget_or_createする。キャッシュ優先、なければINSERT。"""
        code = parsed.get("jravan_horse_code", "")
        if code in self._horse_cache:
            return self._horse_cache[code]

        horse = Horse(
            name=parsed.get("horse_name", ""),
            sex=parsed.get("sex", ""),
            birthday="",  # SEレコードには誕生日がないため空
            jravan_code=code,
        )
        self.db.add(horse)
        self.db.flush()
        self._horse_cache[code] = horse.id
        return horse.id

    def _get_or_create_jockey(self, code: str, name: str) -> int | None:
        """騎手をget_or_createする。キャッシュ優先、なければINSERT。"""
        if not code:
            return None
        if code in self._jockey_cache:
            return self._jockey_cache[code]

        jockey = Jockey(name=name, jravan_code=code)
        self.db.add(jockey)
        self.db.flush()
        self._jockey_cache[code] = jockey.id
        return jockey.id

    def _get_or_create_trainer(self, code: str, name: str) -> int | None:
        """調教師をget_or_createする。キャッシュ優先、なければINSERT。"""
        if not code:
            return None
        if code in self._trainer_cache:
            return self._trainer_cache[code]

        trainer = Trainer(name=name, jravan_code=code)
        self.db.add(trainer)
        self.db.flush()
        self._trainer_cache[code] = trainer.id
        return trainer.id

    def _get_race_id_by_jravan(self, jravan_race_id: str) -> int | None:
        """jravan_race_id からDBのRace.idを取得する。キャッシュ優先。"""
        if jravan_race_id in self._race_cache:
            return self._race_cache[jravan_race_id]
        race = (
            self.db.query(Race.id)
            .filter(Race.jravan_race_id == jravan_race_id)
            .scalar()
        )
        if race is not None:
            self._race_cache[jravan_race_id] = race
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
                horse_age=parsed.get("horse_age"),
                east_west_code=parsed.get("east_west_code"),
                prev_weight_carried=parsed.get("prev_weight_carried"),
                blinker=parsed.get("blinker"),
                prev_jockey_code=parsed.get("prev_jockey_code"),
                jockey_apprentice_code=parsed.get("jockey_apprentice_code"),
            )
            .on_conflict_do_update(
                index_elements=["race_id", "horse_number"],
                set_={
                    "jockey_id": jockey_id,
                    "trainer_id": trainer_id,
                    "weight_carried": parsed.get("weight_carried"),
                    "horse_weight": parsed.get("horse_weight"),
                    "weight_change": parsed.get("weight_change"),
                    "horse_age": parsed.get("horse_age"),
                    "east_west_code": parsed.get("east_west_code"),
                    "prev_weight_carried": parsed.get("prev_weight_carried"),
                    "blinker": parsed.get("blinker"),
                    "prev_jockey_code": parsed.get("prev_jockey_code"),
                    "jockey_apprentice_code": parsed.get("jockey_apprentice_code"),
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
                    arrival_position=parsed.get("arrival_position"),
                    dead_heat=parsed.get("dead_heat"),
                    margin_code=parsed.get("margin_code"),
                    win_odds=parsed.get("win_odds"),
                    win_popularity=parsed.get("win_popularity"),
                    prize_money=parsed.get("prize_money"),
                    last_4f=parsed.get("last_4f"),
                    time_diff=parsed.get("time_diff"),
                    running_style=parsed.get("running_style"),
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
                        "arrival_position": parsed.get("arrival_position"),
                        "dead_heat": parsed.get("dead_heat"),
                        "margin_code": parsed.get("margin_code"),
                        "win_odds": parsed.get("win_odds"),
                        "win_popularity": parsed.get("win_popularity"),
                        "prize_money": parsed.get("prize_money"),
                        "last_4f": parsed.get("last_4f"),
                        "time_diff": parsed.get("time_diff"),
                        "running_style": parsed.get("running_style"),
                    },
                )
            )
            self.db.execute(result_stmt)
