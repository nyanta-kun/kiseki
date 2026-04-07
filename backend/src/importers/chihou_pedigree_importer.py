"""地方競馬 血統データインポーター

HN（繁殖馬マスタ）と SK（産駒マスタ）レコードを受け取り、
chihou.pedigrees テーブルへ UPSERT する。

処理フロー:
  1. HN レコードを先に処理して in-memory 辞書を構築:
       {繁殖登録番号: {"name": 馬名, "name_en": 欧字名}}
  2. SK レコードを処理:
       血統登録番号で chihou.horses.umaconn_code を検索し horse_id を取得
       sire_code / dam_sire_code を HN 辞書で名前解決
       chihou.pedigrees テーブルへ UPSERT (horse_id 単位で冪等)

父系統分類ロジック（SIRE_LINE_MAP, SIRE_LINE_TRAITS, classify_sire_line）は
pedigree_importer.py から import して再利用する（コードコピー不可）。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..db.chihou_models import ChihouHorse, ChihouPedigree
from .jvlink_parser import parse_hn, parse_sk
from .pedigree_importer import (
    SIRE_LINE_MAP,  # noqa: F401 — 再エクスポート用
    SIRE_LINE_TRAITS,  # noqa: F401 — 再エクスポート用
    classify_sire_line,
)

logger = logging.getLogger(__name__)


class ChihouPedigreeImporter:
    """地方競馬 血統データインポーター。

    HN/SK レコードを受け取り chihou.pedigrees テーブルへ UPSERT する。
    重複実行に対して冪等（horse_id 単位で ON CONFLICT UPDATE）。
    馬の検索には ChihouHorse.umaconn_code を使用する。
    父系統分類は pedigree_importer.py の classify_sire_line() を再利用する。
    """

    def __init__(self, db: Session) -> None:
        """初期化。

        Args:
            db: SQLAlchemy 同期セッション（血統インポートは同期処理）
        """
        self.db = db

    # ------------------------------------------------------------------
    # 公開インターフェース
    # ------------------------------------------------------------------

    def import_records(self, records: list[dict[str, str]]) -> dict[str, int]:
        """HN/SK レコードをパースして chihou.pedigrees テーブルへ UPSERT する。

        Args:
            records: [{"rec_id": "HN"|"SK", "data": "..."}, ...] のリスト

        Returns:
            {"hn_parsed": int, "sk_imported": int, "sk_skipped": int}
        """
        # --- Step 1: HN レコードを in-memory 辞書へ展開 ---
        hn_map: dict[str, dict[str, str]] = {}
        hn_parsed = 0
        for rec in records:
            rec_id = rec.get("rec_id", "")
            if rec_id != "HN":
                continue
            parsed = parse_hn(rec.get("data", ""))
            if parsed is None or parsed.get("data_type") == "0":
                continue
            code = parsed["breeding_code"]
            hn_map[code] = {
                "name": parsed["name"],
                "name_en": parsed["name_en"],
            }
            hn_parsed += 1

        logger.info(f"HN辞書構築完了: {hn_parsed} 件")

        # --- Step 2: SK レコードを UPSERT ---
        imported = 0
        skipped = 0
        for rec in records:
            rec_id = rec.get("rec_id", "")
            if rec_id != "SK":
                continue
            parsed = parse_sk(rec.get("data", ""))
            if parsed is None or parsed.get("data_type") == "0":
                skipped += 1
                continue

            blood_code = parsed["blood_code"]
            horse = (
                self.db.query(ChihouHorse)
                .filter(ChihouHorse.umaconn_code == blood_code)
                .first()
            )
            if horse is None:
                # レースデータより先に血統データが来た場合はスキップ
                skipped += 1
                continue

            # 父名・母名・母父名を HN 辞書から解決
            sire_name = hn_map.get(parsed["sire_code"], {}).get("name", "")
            dam_name = hn_map.get(parsed["dam_code"], {}).get("name", "")
            dam_sire_name = hn_map.get(parsed["dam_sire_code"], {}).get("name", "")

            sire_line = classify_sire_line(sire_name)
            dam_sire_line = classify_sire_line(dam_sire_name)

            self._upsert(
                horse_id=horse.id,
                sire=sire_name or None,
                dam=dam_name or None,
                sire_of_dam=dam_sire_name or None,
                sire_line=sire_line if sire_line != "不明" else None,
                dam_sire_line=dam_sire_line if dam_sire_line != "不明" else None,
            )
            imported += 1

        self.db.flush()
        logger.info(f"chihou.pedigrees UPSERT完了: imported={imported} skipped={skipped}")
        return {"hn_parsed": hn_parsed, "sk_imported": imported, "sk_skipped": skipped}

    # ------------------------------------------------------------------
    # 内部メソッド
    # ------------------------------------------------------------------

    def _upsert(
        self,
        horse_id: int,
        sire: str | None,
        dam: str | None,
        sire_of_dam: str | None,
        sire_line: str | None,
        dam_sire_line: str | None,
    ) -> None:
        """chihou.pedigrees テーブルへ UPSERT する（horse_id で一意）。

        UniqueConstraint uq_chihou_pedigree_horse_id（index_elements=["horse_id"]）を利用する。

        Args:
            horse_id: chihou.horses.id
            sire: 父馬名（日本語）
            dam: 母馬名（日本語）
            sire_of_dam: 母父馬名（日本語）
            sire_line: 父系統名（classify_sire_line() の結果）
            dam_sire_line: 母父系統名（classify_sire_line() の結果）
        """
        stmt: Any = (
            insert(ChihouPedigree)
            .values(
                horse_id=horse_id,
                sire=sire,
                dam=dam,
                sire_of_dam=sire_of_dam,
                sire_line=sire_line,
                dam_sire_line=dam_sire_line,
            )
            .on_conflict_do_update(
                index_elements=["horse_id"],
                set_={
                    "sire": sire,
                    "dam": dam,
                    "sire_of_dam": sire_of_dam,
                    "sire_line": sire_line,
                    "dam_sire_line": dam_sire_line,
                },
            )
        )
        self.db.execute(stmt)
