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
        stmt = pg_insert(model).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=list(_KEY_COLS),
            set_={c: getattr(stmt.excluded, c) for c in update_cols},
        )
        await self.db.execute(stmt)
        return len(rows)
