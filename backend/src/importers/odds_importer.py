"""オッズインポーター

O1-O6レコードをパースしてOddsHistoryテーブルへ格納する。
各券種のオッズは固定長テキスト内に連続して格納されているため、
種別ごとにスライス幅を変えて展開する。

JVDF v4.9 速報系 DataSpec と対応するレコード種別 ID (2024-08-07確認):
  0B31 → O1: 単勝・複勝・枠連 (962バイト)
  0B32 → O2: 馬連 (2042バイト)
  0B33 → O3: ワイド (2654バイト)
  0B34 → O4: 馬単 (4031バイト)
  0B35 → O5: 三連複 (12293バイト)
  0B36 → O6: 三連単 (83285バイト)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import OddsHistory, Race
from .jvlink_parser import parse_odds

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# O1（単複枠）レコード内部構造（JVDF v4.9 仕様書準拠）
# O1レコード長: 962バイト
#   共通ヘッダー: pos 1-27 (27バイト)
#   発表月日時分: pos 28-35 (8バイト)
#   登録頭数:    pos 36-37 (2バイト)
#   出走頭数:    pos 38-39 (2バイト)
#   発売フラグ:  pos 40-42 (3バイト: 単勝/複勝/枠連)
#   複勝着払キー: pos 43 (1バイト)
#   単勝オッズ: pos 44, 28頭 × 8byte (馬番2+オッズ4+人気順2)
#   複勝オッズ: pos 268, 28頭 × 12byte (馬番2+最低4+最高4+人気順2)
#   枠連オッズ: pos 604, 36組 × 9byte (組番2+オッズ5+人気順2)
# オッズ値 4桁: "0022" = 2.2倍 / "9999" = 999.9倍以上
# 特殊値: "0000"=無投票 "----"=発売前取消 "****"=発売後取消
# -------------------------------------------------------------------

# O1 単勝: pos44(1-indexed), 28頭 × 8byte(馬番2+オッズ4+人気順2)
WIN_ODDS_START = 44  # 1-indexed
WIN_ENTRY_SIZE = 8  # bytes per entry
WIN_ODDS_OFFSET = 2  # オッズは各エントリの2バイト目から
WIN_ODDS_LEN = 4  # オッズフィールドは4バイト
WIN_MAX_HORSES = 28  # 最大28頭

# O1 複勝: pos268(1-indexed), 28頭 × 12byte(馬番2+最低4+最高4+人気順2)
PLACE_ODDS_START = 268  # 1-indexed
PLACE_ENTRY_SIZE = 12  # bytes per entry
PLACE_ODDS_OFFSET = 2  # 最低オッズは各エントリの2バイト目から
PLACE_ODDS_LEN = 4  # 最低・最高それぞれ4バイト
PLACE_MAX_HORSES = 28  # 最大28頭

# -------------------------------------------------------------------
# O2-O6 エキゾチックオッズ レコード構造 (JVDF v4.9 仕様書準拠)
#
# O2-O6 共通ヘッダー (51バイト):
#   pos 1-2:   レコード種別ID ("O2"〜"O6")
#   pos 3:     データ区分
#   pos 4-27:  共通ヘッダー (JVDF標準: 開催年・月日・場コード等)
#   pos 28-35: 発表月日時分 (8バイト)
#   pos 36-37: 登録頭数 (2バイト)
#   pos 38-39: 出走頭数 (2バイト)
#   pos 40:    発売フラグ (1バイト)
#   pos 41-51: 予備等 (11バイト)
#   pos 52:    オッズデータ開始 ← 各券種ともここから
#
# 検証: 2042-1989-2=51, 2654-2601-2=51, 4031-3978-2=51,
#       12293-12240-2=51, 83285-83232-2=51 (全券種ヘッダー51バイト確認)
#
# O2 (馬連, 2042バイト):  153組 × 13byte (組番4+オッズ6+人気順3)
# O3 (ワイド, 2654バイト): 153組 × 17byte (組番4+最低5+最高5+人気順3)
# O4 (馬単, 4031バイト):  306組 × 13byte (組番4+オッズ6+人気順3)
# O5 (三連複, 12293バイト): 816組 × 15byte (組番6+オッズ6+人気順3)
# O6 (三連単, 83285バイト): 4896組 × 17byte (組番6+オッズ7+人気順4)
#
# オッズ値:
#   6桁: "000022" = 2.2倍 (÷10) / "999999" = 999.9倍以上
#   7桁: "0000022" = 2.2倍 (÷10) / "9999999" = 999.9倍以上  (O6三連単のみ)
# 特殊値: "000000"=無投票 "------"=発売前取消 "******"=発売後取消
#
# -------------------------------------------------------------------
# データ量見積もり（DB設計上の重要注意事項）:
#   O2(馬連):   153組/レース × 36レース/日 × 288回/日 = 1,587,744行/日
#   O3(ワイド): 153組 × 36 × 288 = 1,587,744行/日
#   O4(馬単):   306組 × 36 × 288 = 3,175,488行/日
#   O5(三連複): 816組 × 36 × 288 = 8,468,352行/日
#   O6(三連単): 4896組 × 36 × 288 = 50,810,112行/日  ← 1日5000万行! DB負荷過大
#
# 【削減策（実装済み）】:
#   1. jvlink_agent.py: 発走前30分以内のレースのみポーリング対象
#      → 全レース(36)→直前レース(~6) で 1/6 に削減
#   2. 直前オッズは latest_odds テーブルで管理（上書き方式）し、
#      odds_history へは 1レース 1スナップショット（発走前最終）のみ格納を推奨。
#      現状の実装は毎回全件 INSERT だが将来は ON CONFLICT DO UPDATE に変更予定。
#   3. O6（三連単）は 4896組 × 6レース × 30秒間隔 でも 1日 ~290万行。
#      全組格納するか上位N人気のみにするかは運用で判断する。
# -------------------------------------------------------------------

EXOTIC_HEADER_SIZE = 51  # O2-O6共通ヘッダーバイト数 (オッズデータはpos52=index51から)

# 上位N人気以内の組のみ格納（三連単はmax4896組なので絞り込み必須）
# None = 全組格納
TRIFECTA_MAX_COMBOS: int | None = 200  # 三連単: 上位200人気のみ格納
TRIO_MAX_COMBOS: int | None = 300      # 三連複: 上位300人気のみ格納
EXACTA_MAX_COMBOS: int | None = None   # 馬単: 全組格納 (最大306組)
QUINELLA_MAX_COMBOS: int | None = None  # 馬連/ワイド: 全組格納 (最大153組)


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


def _parse_exotic_odds_value(raw: str) -> float | None:
    """エキゾチックオッズ文字列（6〜7桁）を float に変換する。

    O2-O5 は 6 桁 (÷10)、O6 三連単は 7 桁 (÷10) で格納される。
    "000000"=無投票, "------"=発売前取消, "******"=発売後取消 → None を返す。

    Args:
        raw: オッズ文字列 (6 または 7 バイト)

    Returns:
        倍率 (float), 無効の場合は None
    """
    s = raw.strip()
    if not s or not s.isdigit():
        return None
    v = int(s)
    if v == 0:
        return None
    return v / 10.0


def _parse_horse_combo(raw: str, n_horses: int) -> str | None:
    """固定長の馬番組み合わせフィールドを "N1-N2[-N3]" 形式に変換する。

    Args:
        raw: 馬番が連続して格納されたバイト列文字列 (各馬番2バイト)
        n_horses: 馬番の数 (2 または 3)

    Returns:
        "3-7" または "3-7-12" 形式の文字列、無効の場合は None
    """
    if len(raw) < n_horses * 2:
        return None
    parts = []
    for i in range(n_horses):
        seg = raw[i * 2 : i * 2 + 2].strip()
        if not seg.isdigit() or int(seg) == 0:
            return None
        parts.append(str(int(seg)))
    return "-".join(parts)


class OddsImporter:
    """O1-O6レコードをOddsHistoryテーブルへ格納するクラス。"""

    def __init__(self, db: AsyncSession) -> None:
        """初期化。

        Args:
            db: 非同期DBセッション
        """
        self.db = db

    async def import_records(self, records: list[dict[str, str]]) -> dict[str, Any]:
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
            if rec_id not in ("O1", "O2", "O3", "O4", "O5", "O6"):
                continue
            try:
                parsed = parse_odds(rec["data"])
                if not parsed:
                    continue

                race_db_id = await self._get_race_id(parsed["jravan_race_id"])
                if race_db_id is None:
                    logger.debug(f"Race not found for odds: {parsed['jravan_race_id']}")
                    continue

                rows = self._extract_odds_rows(
                    rec_id, rec["data"], parsed["bet_type"], race_db_id, now
                )
                if rows:
                    await self.db.execute(insert(OddsHistory), rows)
                    stats["saved"] += len(rows)
                    affected_race_ids.add(race_db_id)

            except Exception as e:
                logger.error(f"Odds import error rec_id={rec_id}: {e}")
                stats["errors"] += 1

        await self.db.flush()
        stats["race_ids"] = list(affected_race_ids)
        return stats

    async def _get_race_id(self, jravan_race_id: str) -> int | None:
        """jravan_race_id からDBのRace.idを取得する。"""
        result = await self.db.execute(
            select(Race.id).where(Race.jravan_race_id == jravan_race_id)
        )
        return result.scalar()

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
            # O1（単複枠）は単勝・複勝の両方を含む1レコード
            rows = self._extract_win(data, "win", race_db_id, fetched_at)
            rows += self._extract_place(data, "place", race_db_id, fetched_at)
        elif rec_id == "O2":
            # 馬連: 153組 × 13byte (組番4+オッズ6+人気順3)
            rows = self._extract_pair_odds(
                data, bet_type, race_db_id, fetched_at,
                n_combos=153, entry_size=13, combo_bytes=4, odds_bytes=6, n_horses=2,
                max_combos=QUINELLA_MAX_COMBOS,
            )
        elif rec_id == "O3":
            # ワイド: 153組 × 17byte (組番4+最低5+最高5+人気順3)
            rows = self._extract_wide_odds(
                data, bet_type, race_db_id, fetched_at,
                n_combos=153,
            )
        elif rec_id == "O4":
            # 馬単: 306組 × 13byte (組番4+オッズ6+人気順3)
            rows = self._extract_pair_odds(
                data, bet_type, race_db_id, fetched_at,
                n_combos=306, entry_size=13, combo_bytes=4, odds_bytes=6, n_horses=2,
                max_combos=EXACTA_MAX_COMBOS,
            )
        elif rec_id == "O5":
            # 三連複: 816組 × 15byte (組番6+オッズ6+人気順3)
            rows = self._extract_pair_odds(
                data, bet_type, race_db_id, fetched_at,
                n_combos=816, entry_size=15, combo_bytes=6, odds_bytes=6, n_horses=3,
                max_combos=TRIO_MAX_COMBOS,
            )
        elif rec_id == "O6":
            # 三連単: 4896組 × 17byte (組番6+オッズ7+人気順4)
            rows = self._extract_pair_odds(
                data, bet_type, race_db_id, fetched_at,
                n_combos=4896, entry_size=17, combo_bytes=6, odds_bytes=7, n_horses=3,
                max_combos=TRIFECTA_MAX_COMBOS,
            )

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
            entry = data[pos : pos + WIN_ENTRY_SIZE]
            horse_no_str = entry[:WIN_ODDS_OFFSET]
            if not horse_no_str.isdigit() or int(horse_no_str) == 0:
                break
            horse_no = int(horse_no_str)
            odds_raw = entry[WIN_ODDS_OFFSET : WIN_ODDS_OFFSET + WIN_ODDS_LEN]
            odds_val = _parse_odds_value(odds_raw)
            if odds_val is not None:
                rows.append(
                    {
                        "race_id": race_id,
                        "bet_type": bet_type,
                        "combination": str(horse_no),
                        "odds": odds_val,
                        "fetched_at": fetched_at,
                    }
                )
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
            entry = data[pos : pos + PLACE_ENTRY_SIZE]
            horse_no_str = entry[:PLACE_ODDS_OFFSET]
            if not horse_no_str.isdigit() or int(horse_no_str) == 0:
                break
            horse_no = int(horse_no_str)
            low_raw = entry[PLACE_ODDS_OFFSET : PLACE_ODDS_OFFSET + PLACE_ODDS_LEN]
            _ = entry[
                PLACE_ODDS_OFFSET + PLACE_ODDS_LEN : PLACE_ODDS_OFFSET + PLACE_ODDS_LEN * 2
            ]
            low = _parse_odds_value(low_raw)
            if low is not None:
                rows.append(
                    {
                        "race_id": race_id,
                        "bet_type": bet_type,
                        "combination": str(horse_no),
                        "odds": low,  # 最低オッズを格納（EV計算・表示用）
                        "fetched_at": fetched_at,
                    }
                )
        return rows

    def _extract_pair_odds(
        self,
        data: str,
        bet_type: str,
        race_id: int,
        fetched_at: datetime,
        *,
        n_combos: int,
        entry_size: int,
        combo_bytes: int,
        odds_bytes: int,
        n_horses: int,
        max_combos: int | None = None,
    ) -> list[dict[str, Any]]:
        """O2/O4/O5/O6 組合せオッズ展開。

        O2-O6 共通ヘッダー (51バイト) の後からオッズデータが始まる。
        各エントリ: 組番(combo_bytes) + オッズ(odds_bytes) + 人気順(entry_size-combo_bytes-odds_bytes)

        Args:
            data: レコード文字列
            bet_type: 券種文字列
            race_id: DBのレースID
            fetched_at: 取得日時
            n_combos: 最大組合せ数
            entry_size: 1エントリのバイト数
            combo_bytes: 組番フィールドのバイト数 (馬番 n_horses × 2)
            odds_bytes: オッズフィールドのバイト数 (6 または 7)
            n_horses: 組合せを構成する馬の数 (2 または 3)
            max_combos: 格納上限組数 (None=全組)。人気順の若い順から max_combos 件のみ格納。

        Returns:
            OddsHistory 行の辞書リスト
        """
        rows = []
        data_start = EXOTIC_HEADER_SIZE  # 0-indexed: pos 52(1-indexed) = index 51(0-indexed)
        limit = max_combos if max_combos is not None else n_combos

        for i in range(n_combos):
            if len(rows) >= limit:
                break
            pos = data_start + i * entry_size
            if pos + entry_size > len(data):
                break

            entry = data[pos : pos + entry_size]
            combo_raw = entry[:combo_bytes]
            odds_raw = entry[combo_bytes : combo_bytes + odds_bytes]

            combo_str = _parse_horse_combo(combo_raw, n_horses)
            if combo_str is None:
                continue

            odds_val = _parse_exotic_odds_value(odds_raw)
            if odds_val is not None:
                rows.append(
                    {
                        "race_id": race_id,
                        "bet_type": bet_type,
                        "combination": combo_str,
                        "odds": odds_val,
                        "fetched_at": fetched_at,
                    }
                )

        return rows

    def _extract_wide_odds(
        self,
        data: str,
        bet_type: str,
        race_id: int,
        fetched_at: datetime,
        *,
        n_combos: int,
    ) -> list[dict[str, Any]]:
        """O3 ワイドオッズ展開。

        O3 ワイド: 153組 × 17byte (組番4+最低5+最高5+人気順3)
        最低オッズを格納（EV計算・表示用）。

        Args:
            data: レコード文字列
            bet_type: "quinella_place"
            race_id: DBのレースID
            fetched_at: 取得日時
            n_combos: 最大組数 (153)

        Returns:
            OddsHistory 行の辞書リスト
        """
        rows = []
        data_start = EXOTIC_HEADER_SIZE  # 0-indexed
        combo_bytes = 4
        low_odds_bytes = 5
        entry_size = 17  # combo4 + low5 + high5 + popularity3

        for i in range(n_combos):
            pos = data_start + i * entry_size
            if pos + entry_size > len(data):
                break

            entry = data[pos : pos + entry_size]
            combo_raw = entry[:combo_bytes]
            low_raw = entry[combo_bytes : combo_bytes + low_odds_bytes]

            combo_str = _parse_horse_combo(combo_raw, 2)
            if combo_str is None:
                continue

            odds_val = _parse_exotic_odds_value(low_raw)
            if odds_val is not None:
                rows.append(
                    {
                        "race_id": race_id,
                        "bet_type": bet_type,
                        "combination": combo_str,
                        "odds": odds_val,
                        "fetched_at": fetched_at,
                    }
                )

        return rows
