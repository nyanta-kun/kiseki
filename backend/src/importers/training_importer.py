"""調教データ（坂路 HC / ウッドチップ WC）インポーター

Windows Agent が SLOP/WOOD DataSpec で取得した HC/WC レコード（raw）を受け取り、
parse_hc / parse_wc でパースして keiba.slope_training / keiba.wood_training へ
一括 UPSERT する。

血統登録番号（horses.jravan_code）で馬に紐付くが、調教データは馬の競走馬登録前に
届く場合があるため FK は張らず blood_reg_no を文字列で保持する。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import SlopeTraining, WoodTraining
from .jvlink_parser import parse_hc, parse_wc

logger = logging.getLogger(__name__)

# 🔴 asyncpg のバインドパラメータ上限。1文に入る行数は 32767 ÷ 列数 で決まる。
#   slope(11列) → 2,978行 / wood(25列) → 1,310行
# Windows Agent は 2,000件ずつ POST してくる（jvlink_agent.py::run_chokyo）ため、
# **wood は 1バッチが上限を超えて必ず落ちる**。SLOP と WOOD は別 DataSpec で
# 取得するのでバッチは均質になり、大きい WOOD ファイルが来た日だけ落ちる。
#   実測 2026-08-24 06:00: WOOD 4,141件 → [0:2000] と [2000:4000] が 500、
#   末尾141件だけ成功（DB の当日 wood 行数と一致）。
# エージェント側のバッチサイズを下げても将来また踏むので、**サーバ側で塞ぐ**。
_MAX_BIND_PARAMS = 32767

# DB カラムに渡すフィールド（rec_id 等のメタを除いた値カラム）
_SLOPE_COLS = (
    "blood_reg_no", "training_date", "training_time", "center",
    "time_4f", "lap_800_600", "time_3f", "lap_600_400",
    "time_2f", "lap_400_200", "lap_200_0",
)
_WOOD_COLS = (
    "blood_reg_no", "training_date", "training_time", "center",
    "wood_course", "wood_direction",
    "time_10f", "lap_2000_1800", "time_9f", "lap_1800_1600",
    "time_8f", "lap_1600_1400", "time_7f", "lap_1400_1200",
    "time_6f", "lap_1200_1000", "time_5f", "lap_1000_800",
    "time_4f", "lap_800_600", "time_3f", "lap_600_400",
    "time_2f", "lap_400_200", "lap_200_0",
)
_KEY_COLS = ("blood_reg_no", "training_date", "training_time", "center")


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一キー（血統登録番号+日付+時刻+トレセン）の重複を後勝ちで排除する。

    PostgreSQL の ON CONFLICT は同一 INSERT 文内の重複キーを処理できないため、
    バッチ内で事前に dedupe する必要がある。
    """
    seen: dict[tuple, dict[str, Any]] = {}
    for r in rows:
        key = tuple(r.get(c) for c in _KEY_COLS)
        seen[key] = r  # 後勝ち
    return list(seen.values())


class TrainingImporter:
    """HC/WC（坂路・ウッド調教）レコードを DB へ取り込む。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def import_records(self, records: list[dict[str, Any]]) -> dict[str, int]:
        """raw レコード（{rec_id, data}）をパースして UPSERT する。

        Args:
            records: [{"rec_id": "HC", "data": "HC..."}, ...]

        Returns:
            {"slope": n, "wood": n, "skipped": n}
        """
        slope_rows: list[dict[str, Any]] = []
        wood_rows: list[dict[str, Any]] = []
        skipped = 0

        for rec in records:
            rec_id = rec.get("rec_id", "")
            data = rec.get("data", "")
            if rec_id == "HC":
                parsed = parse_hc(data)
                if parsed:
                    slope_rows.append({c: parsed.get(c) for c in _SLOPE_COLS})
                else:
                    skipped += 1
            elif rec_id == "WC":
                parsed = parse_wc(data)
                if parsed:
                    wood_rows.append({c: parsed.get(c) for c in _WOOD_COLS})
                else:
                    skipped += 1
            else:
                skipped += 1

        slope_n = await self._upsert(SlopeTraining, _dedupe(slope_rows), _SLOPE_COLS)
        wood_n = await self._upsert(WoodTraining, _dedupe(wood_rows), _WOOD_COLS)

        return {"slope": slope_n, "wood": wood_n, "skipped": skipped}

    async def _upsert(
        self, model: type, rows: list[dict[str, Any]], cols: tuple[str, ...]
    ) -> int:
        """ユニークキー衝突時は値カラムを更新する一括 UPSERT。"""
        if not rows:
            return 0
        update_cols = [c for c in cols if c not in _KEY_COLS]
        chunk = max(1, _MAX_BIND_PARAMS // len(cols))
        for i in range(0, len(rows), chunk):
            part = rows[i:i + chunk]
            stmt = pg_insert(model).values(part)
            stmt = stmt.on_conflict_do_update(
                index_elements=list(_KEY_COLS),
                set_={c: getattr(stmt.excluded, c) for c in update_cols},
            )
            await self.db.execute(stmt)
        return len(rows)
