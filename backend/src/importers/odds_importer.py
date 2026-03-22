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
# オッズレコード内部構造（JVDF v4.x 参照）
# O1（単勝）: ヘッダー後、馬番1〜18 × 各6byte (例: "01800" = 18.0倍)
# O2（複勝）: 馬番 × 2値(下限/上限) 各5byte
# O3（枠連）: 8枠×8枠 / 2 の組み合わせ × 6byte
# O4（馬連）: 馬番ペアの組み合わせ × 6byte
# ...
# 注意: 実際のオッズ格納位置はJVDF仕様書を参照すること
# -------------------------------------------------------------------

# ヘッダー終端位置（1-indexed = 23文字 → Pythonでは23まで）
ODDS_HEADER_END = 23

# O1 単勝: ヘッダー後 24文字目から、馬番1-18 × 6byte
# "000180" = 18.0倍 / "000000" = 発売なし
WIN_ODDS_START = 24  # 1-indexed
WIN_ODDS_WIDTH = 6   # bytes per horse
WIN_MAX_HORSES = 18

# O2 複勝: 馬番1-18 × (下限5byte + 上限5byte) = 10byte
PLACE_ODDS_START = 24
PLACE_ODDS_WIDTH = 10
PLACE_MAX_HORSES = 18


def _parse_odds_value(raw: str) -> float | None:
    """オッズ文字列（例: "000180"）を float に変換する。

    JV-Linkのオッズは10倍値で格納されている（例: "0180" = 18.0倍）。
    "000000" や "00000" は発売なし → None を返す。
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

    def import_records(self, records: list[dict[str, str]]) -> dict[str, int]:
        """オッズレコードリストをDBへ取り込む。

        Args:
            records: [{"rec_id": "O1", "data": "O11..."}, ...]

        Returns:
            {"saved": N, "errors": N}
        """
        stats = {"saved": 0, "errors": 0}
        now = datetime.now()

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

            except Exception as e:
                logger.error(f"Odds import error rec_id={rec_id}: {e}")
                stats["errors"] += 1

        self.db.flush()
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

        ヘッダー後、馬番1-18 × 6byte。
        """
        rows = []
        start = WIN_ODDS_START - 1  # 0-indexed
        for i in range(WIN_MAX_HORSES):
            pos = start + i * WIN_ODDS_WIDTH
            raw = data[pos:pos + WIN_ODDS_WIDTH]
            if len(raw) < WIN_ODDS_WIDTH:
                break
            odds_val = _parse_odds_value(raw)
            if odds_val is not None:
                rows.append({
                    "race_id": race_id,
                    "bet_type": bet_type,
                    "combination": str(i + 1),
                    "odds": odds_val,
                    "fetched_at": fetched_at,
                })
        return rows

    def _extract_place(
        self, data: str, bet_type: str, race_id: int, fetched_at: datetime
    ) -> list[dict[str, Any]]:
        """O2 複勝オッズ展開。

        馬番1-18 × (下限5byte + 上限5byte) = 10byte。
        下限・上限の中間値をオッズとして格納。
        """
        rows = []
        start = PLACE_ODDS_START - 1
        for i in range(PLACE_MAX_HORSES):
            pos = start + i * PLACE_ODDS_WIDTH
            raw = data[pos:pos + PLACE_ODDS_WIDTH]
            if len(raw) < PLACE_ODDS_WIDTH:
                break
            low_raw = raw[:5]
            high_raw = raw[5:]
            low = _parse_odds_value(low_raw)
            high = _parse_odds_value(high_raw)
            if low is not None:
                # 下限〜上限の平均をオッズとして使用
                mid = round((low + (high or low)) / 2, 1)
                rows.append({
                    "race_id": race_id,
                    "bet_type": bet_type,
                    "combination": str(i + 1),
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
