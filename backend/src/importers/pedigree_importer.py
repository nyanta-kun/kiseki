"""血統データインポーター

HN（繁殖馬マスタ）と SK（産駒マスタ）レコードを受け取り、
pedigrees テーブルへ UPSERT する。

処理フロー:
  1. HN レコードを先に処理して in-memory 辞書を構築:
       {繁殖登録番号: {"name": 馬名, "name_en": 欧字名}}
  2. SK レコードを処理:
       血統登録番号で horses テーブルを検索し horse_id を取得
       sire_code / dam_sire_code を HN 辞書で名前解決
       pedigrees テーブルへ UPSERT (horse_id 単位で冪等)

父系統（sire_line）は主要種牡馬の名前から自動分類する。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import BreedingHorse, Horse, Pedigree
from .jvlink_parser import parse_hn, parse_sk

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 父系統分類テーブル
# ---------------------------------------------------------------------------
# 主要種牡馬とその系統分類。
# 馬名（日本語）→ 系統名 のマッピング。
# 未登録の種牡馬は "不明" に分類される。
# ---------------------------------------------------------------------------
SIRE_LINE_MAP: dict[str, str] = {
    # サンデーサイレンス系
    "ディープインパクト": "ディープインパクト系",
    "ハーツクライ": "ハーツクライ系",
    "ステイゴールド": "ステイゴールド系",
    "ゴールドシップ": "ステイゴールド系",
    "オルフェーヴル": "ステイゴールド系",
    "ネオユニヴァース": "ネオユニヴァース系",
    "ダービーフィズ": "ネオユニヴァース系",
    "アドマイヤムーン": "アドマイヤムーン系",
    "スクリーンヒーロー": "スクリーンヒーロー系",
    "ジャスタウェイ": "ハーツクライ系",
    "リオンディーズ": "キングカメハメハ系",
    "サトノダイヤモンド": "ディープインパクト系",
    "エピファネイア": "エピファネイア系",
    "キズナ": "ディープインパクト系",
    "サリオス": "ハーツクライ系",
    "コントレイル": "ディープインパクト系",
    "グランアレグリア": "ディープインパクト系",
    "リアルインパクト": "ディープインパクト系",
    "モーリス": "モーリス系",
    "スワーヴリチャード": "ハーツクライ系",
    "シルバーステート": "ディープインパクト系",
    "ブラックタイド": "ブラックタイド系",
    # ミスタープロスペクター系
    "キングカメハメハ": "キングカメハメハ系",
    "ロードカナロア": "ロードカナロア系",
    "アパパネ": "キングカメハメハ系",
    "ルーラーシップ": "キングカメハメハ系",
    "エイシンフラッシュ": "キングカメハメハ系",
    "ドゥラメンテ": "キングカメハメハ系",
    "ダノンバラード": "キングカメハメハ系",
    "シニスターミニスター": "ミスタープロスペクター系",
    "ゴールドアリュール": "サンデーサイレンス系",  # 実際はSS系だが便宜上
    "ホッコータルマエ": "ミスタープロスペクター系",
    "コパノリッキー": "ミスタープロスペクター系",
    "カネヒキリ": "フジキセキ系",
    "フジキセキ": "フジキセキ系",
    "ダイワメジャー": "サンデーサイレンス系",
    # ノーザンダンサー系
    "クロフネ": "クロフネ系",
    "タイキシャトル": "タイキシャトル系",
    "タニノギムレット": "ロベルト系",
    "ウォーエンブレム": "ミスタープロスペクター系",
    "マーベラスサンデー": "サンデーサイレンス系",
    "メジロマックイーン": "マイバブー系",
    "スペシャルウィーク": "サンデーサイレンス系",
    "マンハッタンカフェ": "サンデーサイレンス系",
    "フレンチデピュティ": "フレンチデピュティ系",
    "ヴァーミリアン": "フレンチデピュティ系",
    "スウェプトオーヴァーボード": "ミスタープロスペクター系",
    "ヘニーヒューズ": "ミスタープロスペクター系",
    "パイロ": "ミスタープロスペクター系",
    "グレナディアガーズ": "ファストネットロック系",
    "イクイノックス": "キタサンブラック系",
    "キタサンブラック": "キタサンブラック系",
    "ラブリーデイ": "キングカメハメハ系",
    "ミッキーアイル": "ディープインパクト系",
    "サウスヴィグラス": "ホワイトマズル系",
    "リーチザクラウン": "サンデーサイレンス系",
}

# ---------------------------------------------------------------------------
# 種牡馬適性データ
# ---------------------------------------------------------------------------
# 各系統の surface/distance 適性スコア（内部計算用）
# surface: "turf"=芝, "dirt"=ダート, "both"=両方得意
# dist_pref: 得意距離帯リスト ["sprint","mile","middle","long"]
# ---------------------------------------------------------------------------
SIRE_LINE_TRAITS: dict[str, dict[str, Any]] = {
    "ディープインパクト系": {"surface": "turf", "dist_pref": ["middle", "long", "mile"]},
    "ハーツクライ系": {"surface": "turf", "dist_pref": ["middle", "long"]},
    "ステイゴールド系": {"surface": "turf", "dist_pref": ["middle", "long"]},
    "ネオユニヴァース系": {"surface": "turf", "dist_pref": ["middle"]},
    "マンハッタンカフェ系": {"surface": "turf", "dist_pref": ["long", "middle"]},
    "アドマイヤムーン系": {"surface": "turf", "dist_pref": ["mile", "middle"]},
    "スクリーンヒーロー系": {"surface": "turf", "dist_pref": ["middle", "long"]},
    "エピファネイア系": {"surface": "turf", "dist_pref": ["middle", "long"]},
    "ドゥラメンテ系": {"surface": "turf", "dist_pref": ["mile", "middle"]},
    "モーリス系": {"surface": "turf", "dist_pref": ["mile", "middle"]},
    "ブラックタイド系": {"surface": "turf", "dist_pref": ["middle"]},
    "キタサンブラック系": {"surface": "turf", "dist_pref": ["middle", "long"]},
    "キングカメハメハ系": {"surface": "both", "dist_pref": ["mile", "middle"]},
    "ロードカナロア系": {"surface": "turf", "dist_pref": ["sprint", "mile"]},
    "フジキセキ系": {"surface": "both", "dist_pref": ["sprint", "mile"]},
    "クロフネ系": {"surface": "dirt", "dist_pref": ["sprint", "mile", "middle"]},
    "フレンチデピュティ系": {"surface": "dirt", "dist_pref": ["sprint", "mile"]},
    "ミスタープロスペクター系": {"surface": "dirt", "dist_pref": ["sprint", "mile", "middle"]},
    "サンデーサイレンス系": {"surface": "turf", "dist_pref": ["mile", "middle", "long"]},
    "タイキシャトル系": {"surface": "turf", "dist_pref": ["sprint", "mile"]},
    "ロベルト系": {"surface": "turf", "dist_pref": ["middle", "long"]},
    "ホワイトマズル系": {"surface": "dirt", "dist_pref": ["sprint", "mile"]},
    "マイバブー系": {"surface": "turf", "dist_pref": ["long"]},
    "ファストネットロック系": {"surface": "turf", "dist_pref": ["sprint", "mile"]},
    "不明": {"surface": "both", "dist_pref": ["sprint", "mile", "middle", "long"]},
}


def classify_sire_line(sire_name: str | None) -> str:
    """種牡馬名から父系統名を返す。

    Args:
        sire_name: 種牡馬名（日本語）

    Returns:
        系統名。未登録の場合は "不明"。
    """
    if not sire_name:
        return "不明"
    return SIRE_LINE_MAP.get(sire_name.strip(), "不明")


class PedigreeImporter:
    """血統データインポーター。

    HN/SK レコードを受け取り pedigrees テーブルへ UPSERT する。
    重複実行に対して冪等（horse_id 単位で ON CONFLICT UPDATE）。

    HN レコードは keiba.breeding_horses テーブルに永続化するため、
    プロセス再起動後も繁殖登録番号 → 馬名の変換が可能。
    _global_hn_cache はプロセス内の高速ルックアップ用キャッシュとして補助的に使用する。
    """

    # プロセス内で共有する繁殖馬マスタキャッシュ（HN レコードの累積辞書）
    _global_hn_cache: dict[str, dict[str, str]] = {}

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # 公開インターフェース
    # ------------------------------------------------------------------

    async def import_records(self, records: list[dict[str, str]]) -> dict[str, int]:
        """HN/SK レコードをパースして pedigrees テーブルへ UPSERT する。

        Args:
            records: [{"rec_id": "HN"|"SK", "data": "..."}, ...] のリスト

        Returns:
            {"hn_parsed": int, "sk_imported": int, "sk_skipped": int}
        """
        # --- Step 1: HN レコードを DB + グローバルキャッシュへ累積 ---
        hn_map: dict[str, dict[str, str]] = dict(PedigreeImporter._global_hn_cache)
        hn_parsed = 0
        hn_upsert_rows: list[dict[str, str | None]] = []
        for rec in records:
            rec_id = rec.get("rec_id", "")
            if rec_id != "HN":
                continue
            parsed = parse_hn(rec.get("data", ""))
            if parsed is None or parsed.get("data_type") == "0":
                continue
            code = parsed["breeding_code"]
            entry = {
                "name": parsed["name"],
                "name_en": parsed["name_en"],
            }
            hn_map[code] = entry
            PedigreeImporter._global_hn_cache[code] = entry
            hn_upsert_rows.append({
                "breeding_code": code,
                "name": parsed["name"] or None,
                "name_en": parsed["name_en"] or None,
            })
            hn_parsed += 1

        # HN レコードを keiba.breeding_horses テーブルへ一括 UPSERT
        if hn_upsert_rows:
            stmt = (
                insert(BreedingHorse)
                .values(hn_upsert_rows)
                .on_conflict_do_update(
                    index_elements=["breeding_code"],
                    set_={"name": text("EXCLUDED.name"), "name_en": text("EXCLUDED.name_en")},
                )
            )
            await self.db.execute(stmt)

        logger.info(f"HN辞書構築完了: {hn_parsed} 件 (累計キャッシュ: {len(PedigreeImporter._global_hn_cache)} 件)")

        # --- Step 2: SK レコードを UPSERT ---
        imported = 0
        skipped = 0

        # SK の sire_code/dam_code で未解決のコードを事前に DB から一括取得
        sk_codes_needed: set[str] = set()
        for rec in records:
            if rec.get("rec_id") != "SK":
                continue
            parsed = parse_sk(rec.get("data", ""))
            if parsed is None or parsed.get("data_type") == "0":
                continue
            for code_key in ("sire_code", "dam_code", "dam_sire_code"):
                code = parsed.get(code_key, "")
                if code and code not in hn_map:
                    sk_codes_needed.add(code)

        # DB から不足分のコードを補完
        if sk_codes_needed:
            rows = await self.db.execute(
                select(BreedingHorse).where(
                    BreedingHorse.breeding_code.in_(list(sk_codes_needed))
                )
            )
            for bh in rows.scalars():
                hn_map[bh.breeding_code] = {"name": bh.name or "", "name_en": bh.name_en or ""}
                PedigreeImporter._global_hn_cache[bh.breeding_code] = hn_map[bh.breeding_code]

        for rec in records:
            rec_id = rec.get("rec_id", "")
            if rec_id != "SK":
                continue
            parsed = parse_sk(rec.get("data", ""))
            if parsed is None or parsed.get("data_type") == "0":
                skipped += 1
                continue

            blood_code = parsed["blood_code"]
            result = await self.db.execute(select(Horse).where(Horse.jravan_code == blood_code))
            horse = result.scalar_one_or_none()
            if horse is None:
                skipped += 1
                continue

            # 父名・母名・母父名を HN 辞書から解決
            sire_name = hn_map.get(parsed["sire_code"], {}).get("name", "")
            dam_name = hn_map.get(parsed["dam_code"], {}).get("name", "")
            dam_sire_name = hn_map.get(parsed["dam_sire_code"], {}).get("name", "")

            sire_line = classify_sire_line(sire_name)
            dam_sire_line = classify_sire_line(dam_sire_name)

            await self._upsert(
                horse_id=horse.id,
                sire=sire_name or None,
                dam=dam_name or None,
                sire_of_dam=dam_sire_name or None,
                sire_line=sire_line if sire_line != "不明" else None,
                dam_sire_line=dam_sire_line if dam_sire_line != "不明" else None,
            )
            imported += 1

        await self.db.flush()
        logger.info(f"pedigrees UPSERT完了: imported={imported} skipped={skipped}")
        return {"hn_parsed": hn_parsed, "sk_imported": imported, "sk_skipped": skipped}

    # ------------------------------------------------------------------
    # 内部メソッド
    # ------------------------------------------------------------------

    async def _upsert(
        self,
        horse_id: int,
        sire: str | None,
        dam: str | None,
        sire_of_dam: str | None,
        sire_line: str | None,
        dam_sire_line: str | None,
    ) -> None:
        """pedigrees テーブルへ UPSERT する（horse_id で一意）。"""
        stmt = (
            insert(Pedigree)
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
        await self.db.execute(stmt)
