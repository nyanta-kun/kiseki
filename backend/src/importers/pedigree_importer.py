"""血統データインポーター

HN（繁殖馬マスタ）と SK（産駒マスタ）レコードを受け取り、
pedigrees テーブルへ UPSERT する。

処理フロー:
  0. UM レコードから祖先名を breeding_horses へ補完し、pedigrees を一括補完
  1. HN レコードを先に処理して in-memory 辞書を構築:
       {繁殖登録番号: {"name": 馬名, "name_en": 欧字名}}
  2. SK レコードを一括処理:
       全 blood_code を IN句で一括 SELECT → horse_id を取得
       sire_code / dam_sire_code を HN 辞書で名前解決
       pedigrees テーブルへ一括 UPSERT (horse_id 単位で冪等)

父系統（sire_line）は主要種牡馬の名前から自動分類する。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import BreedingHorse, Horse, Pedigree
from .jvlink_parser import parse_hn, parse_sk, parse_um

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 父系統分類テーブル
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
    """種牡馬名から父系統名を返す。未登録の場合は "不明"。"""
    if not sire_name:
        return "不明"
    return SIRE_LINE_MAP.get(sire_name.strip(), "不明")


class PedigreeImporter:
    """血統データインポーター。

    HN/SK/UM レコードを受け取り pedigrees テーブルへ UPSERT する。
    重複実行に対して冪等（horse_id 単位で ON CONFLICT UPDATE）。

    HN レコードは keiba.breeding_horses テーブルに永続化するため、
    プロセス再起動後も繁殖登録番号 → 馬名の変換が可能。
    _global_hn_cache はプロセス内の高速ルックアップ用キャッシュとして補助的に使用する。
    """

    _global_hn_cache: dict[str, dict[str, str]] = {}

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def import_records(self, records: list[dict[str, str]]) -> dict[str, int]:
        """HN/SK/UM レコードをパースして pedigrees テーブルへ UPSERT する。

        Args:
            records: [{"rec_id": "HN"|"SK"|"UM", "data": "..."}, ...] のリスト

        Returns:
            {"hn_parsed": int, "sk_imported": int, "sk_skipped": int,
             "um_ancestors": int, "um_pedigrees": int}
        """
        # ------------------------------------------------------------------
        # Step 0: UM レコード処理
        #   - 祖先データを breeding_horses へ一括補完
        #   - 馬自身の jravan_code + 祖先名から pedigrees を一括補完（NULL フィールドのみ）
        # ------------------------------------------------------------------
        um_ancestor_rows: list[dict[str, str | None]] = []
        um_jravan_codes: list[str] = []
        um_ancestors_map: dict[str, list[dict[str, str]]] = {}  # jravan_code -> ancestors

        for rec in records:
            if rec.get("rec_id") != "UM":
                continue
            parsed_um = parse_um(rec.get("data", ""))
            if parsed_um is None:
                continue
            ancestors = parsed_um.get("ancestors", [])
            for ancestor in ancestors:
                code = ancestor.get("breeding_code", "")
                name = ancestor.get("name", "")
                if code:
                    um_ancestor_rows.append({"breeding_code": code, "name": name or None, "name_en": None})
            jravan_code = parsed_um.get("jravan_code", "")
            if jravan_code and ancestors:
                um_jravan_codes.append(jravan_code)
                um_ancestors_map[jravan_code] = ancestors

        # breeding_horses 一括 UPSERT
        um_ancestors = 0
        if um_ancestor_rows:
            seen: set[str] = set()
            deduped: list[dict[str, str | None]] = []
            for row in um_ancestor_rows:
                if row["breeding_code"] not in seen:
                    seen.add(row["breeding_code"])  # type: ignore[arg-type]
                    deduped.append(row)
            await self.db.execute(
                insert(BreedingHorse)
                .values(deduped)
                .on_conflict_do_update(
                    index_elements=["breeding_code"],
                    set_={"name": text("COALESCE(EXCLUDED.name, breeding_horses.name)")},
                )
            )
            um_ancestors = len(deduped)
            logger.info(f"UM由来 breeding_horses upsert: {um_ancestors} 件")

        # pedigrees 一括補完（NULL フィールドのみ。既存の非 NULL は保持）
        um_pedigree_count = 0
        if um_jravan_codes:
            horse_rows = await self.db.execute(
                select(Horse.jravan_code, Horse.id).where(Horse.jravan_code.in_(um_jravan_codes))
            )
            um_code_to_id: dict[str, int] = {row.jravan_code: row.id for row in horse_rows}

            um_pedigree_rows: list[dict] = []
            for jravan_code, ancestors in um_ancestors_map.items():
                horse_id = um_code_to_id.get(jravan_code)
                if horse_id is None:
                    continue
                sire = (ancestors[0]["name"] if len(ancestors) > 0 else "") or None
                dam = (ancestors[1]["name"] if len(ancestors) > 1 else "") or None
                sire_of_dam = (ancestors[4]["name"] if len(ancestors) > 4 else "") or None
                sire_line = classify_sire_line(sire) if sire else None
                dam_sire_line = classify_sire_line(sire_of_dam) if sire_of_dam else None
                um_pedigree_rows.append({
                    "horse_id": horse_id,
                    "sire": sire,
                    "dam": dam,
                    "sire_of_dam": sire_of_dam,
                    "sire_line": sire_line if sire_line != "不明" else None,
                    "dam_sire_line": dam_sire_line if dam_sire_line != "不明" else None,
                })
                um_pedigree_count += 1

            if um_pedigree_rows:
                await self.db.execute(
                    insert(Pedigree)
                    .values(um_pedigree_rows)
                    .on_conflict_do_update(
                        index_elements=["horse_id"],
                        set_={
                            "sire": text("COALESCE(pedigrees.sire, EXCLUDED.sire)"),
                            "dam": text("COALESCE(pedigrees.dam, EXCLUDED.dam)"),
                            "sire_of_dam": text("COALESCE(pedigrees.sire_of_dam, EXCLUDED.sire_of_dam)"),
                            "sire_line": text("COALESCE(pedigrees.sire_line, EXCLUDED.sire_line)"),
                            "dam_sire_line": text("COALESCE(pedigrees.dam_sire_line, EXCLUDED.dam_sire_line)"),
                        },
                    )
                )
                logger.info(f"UM由来 pedigrees upsert: {um_pedigree_count} 件")

        # ------------------------------------------------------------------
        # Step 1: HN レコード処理
        #   - in-memory 辞書 + グローバルキャッシュへ累積
        #   - breeding_horses テーブルへ一括 UPSERT
        # ------------------------------------------------------------------
        hn_map: dict[str, dict[str, str]] = dict(PedigreeImporter._global_hn_cache)
        hn_parsed = 0
        hn_upsert_rows: list[dict[str, str | None]] = []

        for rec in records:
            if rec.get("rec_id", "") != "HN":
                continue
            parsed = parse_hn(rec.get("data", ""))
            if parsed is None or parsed.get("data_type") == "0":
                continue
            code = parsed["breeding_code"]
            entry = {"name": parsed["name"], "name_en": parsed["name_en"]}
            hn_map[code] = entry
            PedigreeImporter._global_hn_cache[code] = entry
            hn_upsert_rows.append({
                "breeding_code": code,
                "name": parsed["name"] or None,
                "name_en": parsed["name_en"] or None,
            })
            hn_parsed += 1

        if hn_upsert_rows:
            await self.db.execute(
                insert(BreedingHorse)
                .values(hn_upsert_rows)
                .on_conflict_do_update(
                    index_elements=["breeding_code"],
                    set_={"name": text("EXCLUDED.name"), "name_en": text("EXCLUDED.name_en")},
                )
            )

        logger.info(f"HN辞書構築完了: {hn_parsed} 件 (累計キャッシュ: {len(PedigreeImporter._global_hn_cache)} 件)")

        # ------------------------------------------------------------------
        # Step 2: SK レコード処理（一括化）
        #   1回パス: 全 SK を解析し blood_code + 必要コードを収集
        #   1 SELECT: horses テーブルから jravan_code IN (...) で一括取得
        #   1 SELECT: breeding_horses から不足コードを補完
        #   1 UPSERT: pedigrees テーブルへ全行を一括 INSERT...ON CONFLICT
        # ------------------------------------------------------------------
        imported = 0
        skipped = 0

        sk_parsed_list: list[dict] = []
        sk_blood_codes: list[str] = []
        sk_needed_codes: set[str] = set()

        for rec in records:
            if rec.get("rec_id", "") != "SK":
                continue
            parsed = parse_sk(rec.get("data", ""))
            if parsed is None or parsed.get("data_type") == "0":
                skipped += 1
                continue
            sk_parsed_list.append(parsed)
            sk_blood_codes.append(parsed["blood_code"])
            for code_key in ("sire_code", "dam_code", "dam_sire_code"):
                code = parsed.get(code_key, "")
                if code and code not in hn_map:
                    sk_needed_codes.add(code)

        # breeding_horses から不足コードを一括補完
        if sk_needed_codes:
            bh_rows = await self.db.execute(
                select(BreedingHorse).where(BreedingHorse.breeding_code.in_(list(sk_needed_codes)))
            )
            for bh in bh_rows.scalars():
                entry = {"name": bh.name or "", "name_en": bh.name_en or ""}
                hn_map[bh.breeding_code] = entry
                PedigreeImporter._global_hn_cache[bh.breeding_code] = entry

        # horses を一括 SELECT（N+1 解消）
        sk_code_to_horse_id: dict[str, int] = {}
        if sk_blood_codes:
            horse_rows = await self.db.execute(
                select(Horse.jravan_code, Horse.id).where(Horse.jravan_code.in_(sk_blood_codes))
            )
            sk_code_to_horse_id = {row.jravan_code: row.id for row in horse_rows}

        # pedigrees データを構築
        pedigree_rows: list[dict] = []
        for parsed in sk_parsed_list:
            horse_id = sk_code_to_horse_id.get(parsed["blood_code"])
            if horse_id is None:
                skipped += 1
                continue
            sire_name = hn_map.get(parsed["sire_code"], {}).get("name", "")
            dam_name = hn_map.get(parsed["dam_code"], {}).get("name", "")
            dam_sire_name = hn_map.get(parsed["dam_sire_code"], {}).get("name", "")
            sire_line = classify_sire_line(sire_name)
            dam_sire_line = classify_sire_line(dam_sire_name)
            pedigree_rows.append({
                "horse_id": horse_id,
                "sire": sire_name or None,
                "dam": dam_name or None,
                "sire_of_dam": dam_sire_name or None,
                "sire_line": sire_line if sire_line != "不明" else None,
                "dam_sire_line": dam_sire_line if dam_sire_line != "不明" else None,
            })
            imported += 1

        # pedigrees 一括 UPSERT（新しい非 NULL 値で上書き、NULL なら既存を保持）
        if pedigree_rows:
            await self.db.execute(
                insert(Pedigree)
                .values(pedigree_rows)
                .on_conflict_do_update(
                    index_elements=["horse_id"],
                    set_={
                        "sire": text("COALESCE(EXCLUDED.sire, pedigrees.sire)"),
                        "dam": text("COALESCE(EXCLUDED.dam, pedigrees.dam)"),
                        "sire_of_dam": text("COALESCE(EXCLUDED.sire_of_dam, pedigrees.sire_of_dam)"),
                        "sire_line": text("COALESCE(EXCLUDED.sire_line, pedigrees.sire_line)"),
                        "dam_sire_line": text("COALESCE(EXCLUDED.dam_sire_line, pedigrees.dam_sire_line)"),
                    },
                )
            )

        await self.db.flush()
        logger.info(f"pedigrees UPSERT完了: imported={imported} skipped={skipped}")
        return {
            "hn_parsed": hn_parsed,
            "sk_imported": imported,
            "sk_skipped": skipped,
            "um_ancestors": um_ancestors,
            "um_pedigrees": um_pedigree_count,
        }
