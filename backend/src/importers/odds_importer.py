"""オッズインポーター

O1-O8レコードをパースしてOddsHistoryテーブルへ格納する。
各券種のオッズは固定長テキスト内に連続して格納されているため、
種別ごとにスライス幅を変えて展開する。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..db.models import OddsHistory, Race
from .jvlink_parser import parse_odds

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# オッズレコード内部構造（JVDF v4.9 仕様書準拠）
# O1（単複枠）レコード長: 962バイト
#   単勝オッズ: pos 44, 28頭 × 8byte (馬番2+オッズ4+人気順2)
#   複勝オッズ: pos 268, 28頭 × 12byte (馬番2+最低4+最高4+人気順2)
#   枠連オッズ: pos 604, 36組 × 9byte (組番2+オッズ5+人気順2)
# オッズ値 4桁: "0022" = 2.2倍 / "9999" = 999.9倍以上
# 特殊値: "0000"=無投票 "----"=発売前取消 "****"=発売後取消
# -------------------------------------------------------------------

# O1 単勝: pos44(1-indexed), 28頭 × 8byte(馬番2+オッズ4+人気順2)
WIN_ODDS_START = 44   # 1-indexed
WIN_ENTRY_SIZE = 8    # bytes per entry
WIN_ODDS_OFFSET = 2   # オッズは各エントリの2バイト目から
WIN_ODDS_LEN = 4      # オッズフィールドは4バイト
WIN_MAX_HORSES = 28   # 最大28頭

# O1 複勝: pos268(1-indexed), 28頭 × 12byte(馬番2+最低4+最高4+人気順2)
PLACE_ODDS_START = 268  # 1-indexed
PLACE_ENTRY_SIZE = 12   # bytes per entry
PLACE_ODDS_OFFSET = 2   # 最低オッズは各エントリの2バイト目から
PLACE_ODDS_LEN = 4      # 最低・最高それぞれ4バイト
PLACE_MAX_HORSES = 28   # 最大28頭


def _parse_odds_value(raw: str) -> float | None:
    """オッズ文字列（4桁, 例: "0022"）を float に変換する。

    JV-Linkのオッズは10倍値で格納されている（例: "0022" = 2.2倍）。
    "0000"=無投票 / "----"=発売前取消 / "****"=発売後取消 → None を返す。
    "9999"=999.9倍以上 → 999.9 を返す。
    """
    s = raw.strip()
    if not s or not s.isdigit():
        return None
    v = int(s)
    if v == 0:
        return None
    return v / 10.0


class OddsImporter:
    """O1-O8レコードをOddsHistoryテーブルへ格納するクラス。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def import_records(self, records: list[dict[str, str]]) -> dict[str, Any]:
        """オッズレコードリストをDBへ取り込む。

        Args:
            records: [{"rec_id": "O1", "data": "O11..."}, ...]

        Returns:
            {"saved": N, "errors": N, "race_ids": [更新されたrace_idのリスト]}
        """
        stats: dict[str, Any] = {"saved": 0, "errors": 0, "race_ids": []}
        now = datetime.now()
        affected_race_ids: set[int] = set()

        for rec in records:
            rec_id = rec.get("rec_id", "")
            if rec_id not in ("O1", "O2", "O3", "O4", "O5", "O6", "O7", "O8"):
                continue
            try:
                parsed = parse_odds(rec["data"])
                if not parsed:
                    continue

                race_db_id = self._get_race_id(parsed["jravan_race_id"])
                if race_db_id is None:
                    logger.debug(f"Race not found for odds: {parsed['jravan_race_id']}")
                    continue

                rows = self._extract_odds_rows(rec_id, rec["data"], parsed["bet_type"], race_db_id, now)
                if rows:
                    self.db.execute(insert(OddsHistory), rows)
                    stats["saved"] += len(rows)
                    affected_race_ids.add(race_db_id)

            except Exception as e:
                logger.error(f"Odds import error rec_id={rec_id}: {e}")
                stats["errors"] += 1

        self.db.flush()
        stats["race_ids"] = list(affected_race_ids)
        return stats

    def _get_race_id(self, jravan_race_id: str) -> int | None:
        """jravan_race_id からDBのRace.idを取得する。"""
        return (
            self.db.query(Race.id)
            .filter(Race.jravan_race_id == jravan_race_id)
            .scalar()
        )

    def _extract_odds_rows(
        self,
        rec_id: str,
        data: str,
        bet_type: str,
        race_db_id: int,
        fetched_at: datetime,
    ) -> list[dict[str, Any]]:
        """レコード種別ごとにオッズ行を展開する。"""
        rows: list[dict[str, Any]] = []

        if rec_id == "O1":
            rows = self._extract_win(data, bet_type, race_db_id, fetched_at)
        elif rec_id == "O2":
            rows = self._extract_place(data, bet_type, race_db_id, fetched_at)
        elif rec_id in ("O3", "O4", "O5", "O6"):
            rows = self._extract_pair_odds(rec_id, data, bet_type, race_db_id, fetched_at)
        elif rec_id in ("O7", "O8"):
            rows = self._extract_trio_odds(rec_id, data, bet_type, race_db_id, fetched_at)

        return rows

    def _extract_win(
        self, data: str, bet_type: str, race_id: int, fetched_at: datetime
    ) -> list[dict[str, Any]]:
        """O1 単勝オッズ展開。

        pos44(1-indexed)から28頭分、各8byte(馬番2+オッズ4+人気順2)。
        馬番は各エントリに実際の番号が格納されている。
        """
        rows = []
        start = WIN_ODDS_START - 1  # 0-indexed: 43
        for i in range(WIN_MAX_HORSES):
            pos = start + i * WIN_ENTRY_SIZE
            if pos + WIN_ENTRY_SIZE > len(data):
                break
            entry = data[pos:pos + WIN_ENTRY_SIZE]
            horse_no_str = entry[:WIN_ODDS_OFFSET]
            if not horse_no_str.isdigit() or int(horse_no_str) == 0:
                break
            horse_no = int(horse_no_str)
            odds_raw = entry[WIN_ODDS_OFFSET:WIN_ODDS_OFFSET + WIN_ODDS_LEN]
            odds_val = _parse_odds_value(odds_raw)
            if odds_val is not None:
                rows.append({
                    "race_id": race_id,
                    "bet_type": bet_type,
                    "combination": str(horse_no),
                    "odds": odds_val,
                    "fetched_at": fetched_at,
                })
        return rows

    def _extract_place(
        self, data: str, bet_type: str, race_id: int, fetched_at: datetime
    ) -> list[dict[str, Any]]:
        """O1 複勝オッズ展開。

        pos268(1-indexed)から28頭分、各12byte(馬番2+最低4+最高4+人気順2)。
        下限・上限の中間値をオッズとして格納。
        """
        rows = []
        start = PLACE_ODDS_START - 1  # 0-indexed: 267
        for i in range(PLACE_MAX_HORSES):
            pos = start + i * PLACE_ENTRY_SIZE
            if pos + PLACE_ENTRY_SIZE > len(data):
                break
            entry = data[pos:pos + PLACE_ENTRY_SIZE]
            horse_no_str = entry[:PLACE_ODDS_OFFSET]
            if not horse_no_str.isdigit() or int(horse_no_str) == 0:
                break
            horse_no = int(horse_no_str)
            low_raw = entry[PLACE_ODDS_OFFSET:PLACE_ODDS_OFFSET + PLACE_ODDS_LEN]
            high_raw = entry[PLACE_ODDS_OFFSET + PLACE_ODDS_LEN:PLACE_ODDS_OFFSET + PLACE_ODDS_LEN * 2]
            low = _parse_odds_value(low_raw)
            high = _parse_odds_value(high_raw)
            if low is not None:
                mid = round((low + (high or low)) / 2, 1)
                rows.append({
                    "race_id": race_id,
                    "bet_type": bet_type,
                    "combination": str(horse_no),
                    "odds": mid,
                    "fetched_at": fetched_at,
                })
        return rows

    def _extract_pair_odds(
        self, rec_id: str, data: str, bet_type: str, race_id: int, fetched_at: datetime
    ) -> list[dict[str, Any]]:
        """O3/O4/O5/O6 組み合わせオッズ展開（ペア系）。

        組み合わせが連続して格納されている。各オッズは7byte。
        組み合わせキーはレコードデータ内に含まれる（JVDF仕様依存）。
        簡易実装: 全データをraw保存。詳細展開は必要に応じて追加。
        """
        # TODO: 詳細実装。現状はスキップ（行数ゼロを返す）
        # 単勝・複勝のみ即時値利用。連系は必要時に実装。
        return []

    def _extract_trio_odds(
        self, rec_id: str, data: str, bet_type: str, race_id: int, fetched_at: datetime
    ) -> list[dict[str, Any]]:
        """O7/O8 三連系オッズ展開。簡易実装（スキップ）。"""
        return []
