"""地方競馬 レース・出馬表・成績インポーター

RA/SEレコードをパースしてVPS PostgreSQL（chihouスキーマ）へUPSERTする。
重複実行に対して冪等（同一umaconn_race_idが存在する場合は更新）。
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.chihou_models import (
    ChihouHorse,
    ChihouJockey,
    ChihouRace,
    ChihouRaceEntry,
    ChihouRaceResult,
    ChihouTrainer,
)
from .jvlink_parser import parse_ra, parse_se

logger = logging.getLogger(__name__)


# -------------------------------------------------------------------
# 単位変換ヘルパー
# -------------------------------------------------------------------
def _finish_time_to_decimal(raw: int | None) -> Decimal | None:
    """SEレコードのタイムフィールド（0.1秒単位整数）をDecimal秒に変換する。

    JV-VAN SEレコードのタイムは0.1秒単位の整数で格納されている。
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


def _year4(year_str: str) -> str:
    """2桁または4桁の年文字列を4桁に正規化する。"""
    if len(year_str) == 2:
        return f"20{year_str}"
    return year_str[:4]


class ChihouRaceImporter:
    """RA/SEレコードを地方競馬DB（chihouスキーマ）に取り込むクラス。

    UmaConn エージェントから受信した RA（レース情報）・SE（出馬表・成績）レコードを
    chihou.races, chihou.race_entries, chihou.race_results テーブルへ UPSERT する。
    識別キーは jravan_*_code の代わりに umaconn_code / umaconn_race_id を使用する。
    """

    def __init__(self, db: AsyncSession) -> None:
        """初期化。

        Args:
            db: 非同期 SQLAlchemy セッション
        """
        self.db = db
        # リクエスト内キャッシュ（umaconn_code → DB id）
        self._horse_cache: dict[str, int] = {}
        self._jockey_cache: dict[str, int] = {}
        self._trainer_cache: dict[str, int] = {}
        self._race_cache: dict[str, int] = {}

    # ------------------------------------------------------------------
    # 公開メソッド
    # ------------------------------------------------------------------
    async def import_records(self, records: list[dict[str, str]]) -> dict[str, int]:
        """RA/SEレコードのリストをDBへ取り込む（バルクupsert処理）。

        Phase1: 全レコードをパースしてRA/SEを分離
        Phase2: バルクSQL（1ステートメントでN件）でupsert

        Args:
            records: [{"rec_id": "RA", "data": "RA1..."}, ...]

        Returns:
            {"races": N, "entries": N, "results": N, "errors": N, "result_race_ids": [...]}
        """
        stats: dict[str, Any] = {
            "races": 0,
            "entries": 0,
            "results": 0,
            "errors": 0,
            "result_race_ids": [],
        }

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
                await self._bulk_upsert_races(ra_parsed)
                stats["races"] = len(ra_parsed)
            except Exception as e:
                logger.error(f"Bulk race upsert error: {e}")
                stats["errors"] += len(ra_parsed)

        if se_parsed:
            # Phase2b: 既存エンティティをバルクSELECTでキャッシュ構築
            await self._warm_up_caches(se_parsed)

            # Phase2c: 新規エンティティをバルクINSERT DO NOTHINGで登録
            try:
                await self._ensure_entities_bulk(se_parsed)
            except Exception as e:
                logger.error(f"Bulk entity create error: {e}")

            # Phase2d: entriesを1SQLでバルクupsert
            entry_map: dict[tuple[int, int], int] = {}
            try:
                entry_map = await self._bulk_upsert_entries(se_parsed)
                stats["entries"] = len(entry_map)
            except Exception as e:
                logger.error(f"Bulk entry upsert error: {e}")
                stats["errors"] += len(se_parsed)

            # Phase2e: resultsを1SQLでバルクupsert
            if entry_map:
                try:
                    race_ids, count = await self._bulk_upsert_results(se_parsed, entry_map)
                    stats["results"] = count
                    stats["result_race_ids"] = race_ids
                except Exception as e:
                    logger.error(f"Bulk result upsert error: {e}")
                    stats["errors"] += len(se_parsed)

        await self.db.flush()
        return stats

    async def _warm_up_caches(self, se_list: list[dict[str, Any]]) -> None:
        """SE群から必要なコードをバルクSELECTしてキャッシュを構築する。

        VPSへのRTTを最小化するため、N件のSELECTを各1件にまとめる。
        jravan_*_code の代わりに umaconn_code / umaconn_race_id を使用する。
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
            rows = (
                await self.db.execute(
                    select(ChihouHorse.umaconn_code, ChihouHorse.id).where(
                        ChihouHorse.umaconn_code.in_(new_horse_codes)
                    )
                )
            ).fetchall()
            self._horse_cache.update({r.umaconn_code: r.id for r in rows})

        if new_jockey_codes:
            rows = (
                await self.db.execute(
                    select(ChihouJockey.umaconn_code, ChihouJockey.id).where(
                        ChihouJockey.umaconn_code.in_(new_jockey_codes)
                    )
                )
            ).fetchall()
            self._jockey_cache.update({r.umaconn_code: r.id for r in rows})

        if new_trainer_codes:
            rows = (
                await self.db.execute(
                    select(ChihouTrainer.umaconn_code, ChihouTrainer.id).where(
                        ChihouTrainer.umaconn_code.in_(new_trainer_codes)
                    )
                )
            ).fetchall()
            self._trainer_cache.update({r.umaconn_code: r.id for r in rows})

        if new_race_ids:
            rows = (
                await self.db.execute(
                    select(ChihouRace.umaconn_race_id, ChihouRace.id).where(
                        ChihouRace.umaconn_race_id.in_(new_race_ids)
                    )
                )
            ).fetchall()
            self._race_cache.update({r.umaconn_race_id: r.id for r in rows})

    # ------------------------------------------------------------------
    # バルクupsertメソッド（N件を1 SQLで処理）
    # ------------------------------------------------------------------
    async def _bulk_upsert_races(self, ra_list: list[dict[str, Any]]) -> None:
        """RAレコードを1 SQLでバルクupsertし、_race_cacheを更新する。

        パーサー出力の jravan_race_id キーを DB カラム umaconn_race_id にマッピングして格納する。
        個別ループ（N SQL）→ 1 SQL に変換することでVPS RTTを大幅削減。
        """
        values = [
            {
                "umaconn_race_id": p["jravan_race_id"],
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
            "race_name",
            "surface",
            "distance",
            "direction",
            "condition",
            "weather",
            "grade",
            "post_time",
            "race_type_code",
            "weight_type_code",
            "head_count",
            "prize_1st",
            "prize_2nd",
            "prize_3rd",
            "registered_count",
            "finishers_count",
            "first_3f",
            "last_3f_race",
            "lap_times",
            "record_update_type",
            "prev_distance",
            "prev_track_code",
            "prev_grade_code",
            "prev_post_time",
        ]
        stmt = insert(ChihouRace).values(values)
        returning_stmt = stmt.on_conflict_do_update(  # type: ignore[assignment]
            index_elements=["umaconn_race_id"],
            set_={col: stmt.excluded[col] for col in update_cols},
        ).returning(ChihouRace.id, ChihouRace.umaconn_race_id)
        for race_id, umaconn_id in (await self.db.execute(returning_stmt)):
            self._race_cache[umaconn_id] = race_id

    async def _ensure_entities_bulk(self, se_list: list[dict[str, Any]]) -> None:
        """SE群の新規馬・騎手・調教師をバルクINSERTしてキャッシュを補完する。

        _warm_up_cachesでDBに存在するものはキャッシュ済み。
        未キャッシュ（新規）のみ INSERT ON CONFLICT DO NOTHING → SELECT で取得。
        各エンティティ種別最大2 SQL（INSERT + SELECT）で完結する。
        umaconn_code を識別キーとして使用する。
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
                    "umaconn_code": code,
                }
        if new_horses:
            await self.db.execute(
                insert(ChihouHorse).values(list(new_horses.values())).on_conflict_do_nothing()
            )
            rows = (
                await self.db.execute(
                    select(ChihouHorse.umaconn_code, ChihouHorse.id).where(
                        ChihouHorse.umaconn_code.in_(new_horses.keys())
                    )
                )
            ).fetchall()
            self._horse_cache.update({r.umaconn_code: r.id for r in rows})

        # --- 騎手 ---
        new_jockeys: dict[str, dict[str, Any]] = {}
        for p in se_list:
            code = p.get("jravan_jockey_code", "")
            if code and code not in self._jockey_cache and code not in new_jockeys:
                new_jockeys[code] = {"name": p.get("jockey_name", ""), "umaconn_code": code}
        if new_jockeys:
            await self.db.execute(
                insert(ChihouJockey).values(list(new_jockeys.values())).on_conflict_do_nothing()
            )
            rows = (
                await self.db.execute(
                    select(ChihouJockey.umaconn_code, ChihouJockey.id).where(
                        ChihouJockey.umaconn_code.in_(new_jockeys.keys())
                    )
                )
            ).fetchall()
            self._jockey_cache.update({r.umaconn_code: r.id for r in rows})

        # --- 調教師 ---
        new_trainers: dict[str, dict[str, Any]] = {}
        for p in se_list:
            code = p.get("jravan_trainer_code", "")
            if code and code not in self._trainer_cache and code not in new_trainers:
                new_trainers[code] = {"name": p.get("trainer_name", ""), "umaconn_code": code}
        if new_trainers:
            await self.db.execute(
                insert(ChihouTrainer).values(list(new_trainers.values())).on_conflict_do_nothing()
            )
            rows = (
                await self.db.execute(
                    select(ChihouTrainer.umaconn_code, ChihouTrainer.id).where(
                        ChihouTrainer.umaconn_code.in_(new_trainers.keys())
                    )
                )
            ).fetchall()
            self._trainer_cache.update({r.umaconn_code: r.id for r in rows})

    async def _bulk_upsert_entries(
        self, se_list: list[dict[str, Any]]
    ) -> dict[tuple[int, int], int]:
        """SEレコードのChihouRaceEntryを1 SQLでバルクupsertする。

        ON CONFLICT は UniqueConstraint uq_chihou_race_entry_horse_num
        （index_elements=["race_id", "horse_number"]）を使用する。

        Returns:
            {(race_id, horse_number): entry_id} — result upsertで使用するマップ
        """
        values = []
        for p in se_list:
            race_id = self._race_cache.get(p.get("jravan_race_id", ""))
            horse_id = self._horse_cache.get(p.get("jravan_horse_code", ""))
            if not race_id or not horse_id:
                logger.warning(
                    f"Entry skip: race={p.get('jravan_race_id')} "
                    f"horse={p.get('jravan_horse_code')} not in cache"
                )
                continue
            values.append(
                {
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
                }
            )
        if not values:
            return {}
        update_cols = [
            "jockey_id",
            "trainer_id",
            "weight_carried",
            "horse_weight",
            "weight_change",
            "horse_age",
            "east_west_code",
            "prev_weight_carried",
            "blinker",
            "prev_jockey_code",
            "jockey_apprentice_code",
        ]
        stmt = insert(ChihouRaceEntry).values(values)
        returning_stmt = stmt.on_conflict_do_update(  # type: ignore[assignment]
            index_elements=["race_id", "horse_number"],
            set_={col: stmt.excluded[col] for col in update_cols},
        ).returning(
            ChihouRaceEntry.id, ChihouRaceEntry.race_id, ChihouRaceEntry.horse_number
        )
        entry_map: dict[tuple[int, int], int] = {}
        for entry_id, race_id, horse_num in (await self.db.execute(returning_stmt)):
            entry_map[(race_id, horse_num)] = entry_id
        return entry_map

    async def _bulk_upsert_results(
        self, se_list: list[dict[str, Any]], entry_map: dict[tuple[int, int], int]
    ) -> tuple[list[int], int]:
        """SEレコードのChihouRaceResultを1 SQLでバルクupsertする。

        ChihouRaceResult には keiba.race_results にはない margin カラムが存在する。
        パーサー出力に margin がある場合はその値を、なければ None を格納する。
        ON CONFLICT は UniqueConstraint uq_chihou_race_result_horse
        （index_elements=["race_id", "horse_id"]）を使用する。

        Args:
            entry_map: _bulk_upsert_entries()の戻り値

        Returns:
            (成績が保存されたrace_idリスト, upsert件数)
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
            values.append(
                {
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
                    "margin": p.get("margin"),
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
                }
            )
        if not values:
            return [], 0
        result_race_ids = list({v["race_id"] for v in values if v.get("finish_position")})
        update_cols = [
            "finish_position",
            "finish_time",
            "last_3f",
            "margin",
            "passing_1",
            "passing_2",
            "passing_3",
            "passing_4",
            "abnormality_code",
            "jockey_id",
            "arrival_position",
            "dead_heat",
            "margin_code",
            "win_odds",
            "win_popularity",
            "prize_money",
            "last_4f",
            "time_diff",
            "running_style",
        ]
        stmt = insert(ChihouRaceResult).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["race_id", "horse_id"],
            set_={col: stmt.excluded[col] for col in update_cols},
        )
        await self.db.execute(stmt)
        return result_race_ids, len(values)
