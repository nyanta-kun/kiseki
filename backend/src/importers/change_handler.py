"""出走変更ハンドラー

AV（出走取消/競走除外）・JC（騎手変更）レコードをEntryChangeテーブルへ記録し、
必要な再算出フラグを立てる。
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..db.models import EntryChange, Race, RaceEntry
from .jvlink_parser import parse_av, parse_jc

logger = logging.getLogger(__name__)


class ChangeHandler:
    """変更レコードを受けてDBへ記録し、再算出対象を管理するクラス。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def handle(self, change_type: str, raw_data: str) -> dict[str, object]:
        """変更通知を処理する。

        Args:
            change_type: "scratch" | "jockey_change"
            raw_data: JV-Link生レコード文字列

        Returns:
            {"recorded": bool, "recalc_race_id": int | None}
        """
        if change_type == "scratch":
            return self._handle_scratch(raw_data)
        elif change_type == "jockey_change":
            return self._handle_jockey_change(raw_data)
        else:
            logger.warning(f"Unknown change_type: {change_type}")
            return {"recorded": False, "recalc_race_id": None}

    def _get_race_id(self, jravan_race_id: str) -> int | None:
        return (
            self.db.query(Race.id)
            .filter(Race.jravan_race_id == jravan_race_id)
            .scalar()
        )

    def _get_horse_id_by_horse_num(self, race_db_id: int, horse_num: int) -> int | None:
        """馬番からhorse_idを引く。"""
        entry = (
            self.db.query(RaceEntry)
            .filter(
                RaceEntry.race_id == race_db_id,
                RaceEntry.horse_number == horse_num,
            )
            .first()
        )
        return entry.horse_id if entry else None

    def _handle_scratch(self, raw_data: str) -> dict[str, object]:
        """出走取消・競走除外を処理する。

        変更検知ルール:
          出走取消/除外 → そのレース全馬を再算出（CLAUDE.md準拠）
        """
        parsed = parse_av(raw_data)
        if not parsed:
            return {"recorded": False, "recalc_race_id": None}

        race_db_id = self._get_race_id(parsed["jravan_race_id"])
        if race_db_id is None:
            logger.warning(f"Race not found for AV: {parsed['jravan_race_id']}")
            return {"recorded": False, "recalc_race_id": None}

        horse_num = parsed.get("horse_number")
        horse_id = self._get_horse_id_by_horse_num(race_db_id, horse_num) if horse_num else None

        change = EntryChange(
            race_id=race_db_id,
            horse_id=horse_id,
            change_type="scratch",
            old_value=None,
            new_value=parsed.get("detail"),
            recalc_triggered=False,  # 再算出実行後にTrueへ更新
        )
        self.db.add(change)
        self.db.flush()
        logger.info(f"Scratch recorded: race_id={race_db_id}, horse_num={horse_num}")

        return {"recorded": True, "recalc_race_id": race_db_id}

    def _handle_jockey_change(self, raw_data: str) -> dict[str, object]:
        """騎手変更を処理する。

        変更検知ルール:
          騎手変更 → 該当馬の騎手指数 + 全馬の展開指数を再算出（CLAUDE.md準拠）
        """
        parsed = parse_jc(raw_data)
        if not parsed:
            return {"recorded": False, "recalc_race_id": None}

        race_db_id = self._get_race_id(parsed["jravan_race_id"])
        if race_db_id is None:
            logger.warning(f"Race not found for JC: {parsed['jravan_race_id']}")
            return {"recorded": False, "recalc_race_id": None}

        horse_num = parsed.get("horse_number")
        horse_id = self._get_horse_id_by_horse_num(race_db_id, horse_num) if horse_num else None

        change = EntryChange(
            race_id=race_db_id,
            horse_id=horse_id,
            change_type="jockey_change",
            old_value=parsed.get("old_value"),
            new_value=parsed.get("new_value"),
            recalc_triggered=False,
        )
        self.db.add(change)
        self.db.flush()
        logger.info(f"Jockey change recorded: race_id={race_db_id}, horse_num={horse_num}")

        return {"recorded": True, "recalc_race_id": race_db_id, "horse_id": horse_id}
