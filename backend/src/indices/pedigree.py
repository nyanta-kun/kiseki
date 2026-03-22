"""血統指数算出Agent

父（sire）と母父（sire of dam）の系統特性を、
対象レースのコース・距離・馬場と照合して血統適性スコアを算出する。

算出ロジック:
  1. pedigrees テーブルから sire_name・sire_of_dam_name・sire_line を取得
  2. SIRE_LINE_TRAITS テーブルで系統ごとの surface/距離適性を参照
  3. レース条件（surface, distance_category）との適合スコアを計算:
       surface 一致 or "both": +SURFACE_BONUS
       distance_category 一致: +DIST_BONUS
       隣接距離カテゴリ: +DIST_ADJ_BONUS
  4. 父（weight=0.65）と母父（weight=0.35）の加重平均
  5. クリップして 0-100 に収める

制約:
  - pedigrees データが未登録の馬は SPEED_INDEX_MEAN（50.0）を返す
  - 系統が "不明" の場合は NEUTRAL_SCORE（50.0）を適用
  - バッチ処理では race_id 単位で horse_id の一覧を取得し N+1 を回避
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from ..db.models import Pedigree, Race, RaceEntry
from ..importers.pedigree_importer import SIRE_LINE_TRAITS
from ..utils.constants import SPEED_INDEX_MEAN
from .base import IndexCalculator

logger = logging.getLogger(__name__)

# 距離カテゴリ境界（メートル）
DIST_SPRINT_MAX = 1400    # スプリント: ≤1400m
DIST_MILE_MAX = 1800      # マイル: 1401-1800m
DIST_MIDDLE_MAX = 2400    # 中距離: 1801-2400m
# 長距離: >2400m

# スコア加算値
SURFACE_BONUS = 18.0      # surface が合致（turf/dirt）
DIST_BONUS = 15.0         # 距離カテゴリが完全一致
DIST_ADJ_BONUS = 6.0      # 隣接カテゴリ（1段階ズレ）

# 父/母父の重み
SIRE_WEIGHT = 0.65
DAM_SIRE_WEIGHT = 0.35

# ベーススコア（全要素なしの場合）
BASE_SCORE = 38.0

# 未知系統のスコア（ニュートラル）
NEUTRAL_SCORE = SPEED_INDEX_MEAN

# 指数クリップ
INDEX_MIN = 0.0
INDEX_MAX = 100.0

# 距離カテゴリ名
DIST_CATS = ["sprint", "mile", "middle", "long"]

# 隣接カテゴリ判定テーブル
_ADJACENT: dict[str, set[str]] = {
    "sprint": {"mile"},
    "mile":   {"sprint", "middle"},
    "middle": {"mile", "long"},
    "long":   {"middle"},
}


def _dist_category(distance: int) -> str:
    """距離（メートル）をカテゴリ名に変換する。"""
    if distance <= DIST_SPRINT_MAX:
        return "sprint"
    if distance <= DIST_MILE_MAX:
        return "mile"
    if distance <= DIST_MIDDLE_MAX:
        return "middle"
    return "long"


def _surface_key(surface: str | None) -> str:
    """DB の surface 値を "turf"/"dirt" に正規化する。"""
    if surface and surface.startswith("芝"):
        return "turf"
    if surface and surface.startswith("ダ"):
        return "dirt"
    return "unknown"


def _sire_line_score(
    sire_line: str | None,
    race_surface: str,
    dist_cat: str,
) -> float:
    """系統 × レース条件のマッチスコアを算出する（0-100 の raw スコア）。

    Args:
        sire_line: 父系統名（"ディープインパクト系" 等、または None/"不明"）
        race_surface: "turf" | "dirt" | "unknown"
        dist_cat: "sprint" | "mile" | "middle" | "long"

    Returns:
        raw スコア（BASE_SCORE + ボーナス合計）
    """
    if not sire_line or sire_line == "不明":
        return NEUTRAL_SCORE

    traits: dict[str, Any] = SIRE_LINE_TRAITS.get(sire_line, SIRE_LINE_TRAITS["不明"])
    score = BASE_SCORE

    # surface ボーナス
    trait_surface = traits["surface"]
    if trait_surface == "both" or race_surface == "unknown":
        score += SURFACE_BONUS * 0.5  # both は半額（どちらも "普通"）
    elif trait_surface == race_surface:
        score += SURFACE_BONUS
    # else: surface 不一致 → ボーナスなし

    # distance ボーナス
    pref = traits["dist_pref"]
    if dist_cat in pref:
        score += DIST_BONUS
    elif any(dc in pref for dc in _ADJACENT.get(dist_cat, set())):
        score += DIST_ADJ_BONUS

    return min(INDEX_MAX, score)


class PedigreeIndexCalculator(IndexCalculator):
    """血統指数算出Agent。

    IndexCalculator を継承し、単一馬（calculate）と
    レース全馬バッチ（calculate_batch）の両インターフェースを提供する。
    """

    def __init__(self, db: Session) -> None:
        super().__init__(db)

    # ------------------------------------------------------------------
    # 公開インターフェース
    # ------------------------------------------------------------------

    def calculate(self, race_id: int, horse_id: int) -> float:
        """単一馬の血統指数を算出する。

        Args:
            race_id: DB の races.id
            horse_id: DB の horses.id

        Returns:
            血統指数（0-100）。データ未登録時は SPEED_INDEX_MEAN。
        """
        race = self.db.query(Race).filter(Race.id == race_id).first()
        if not race:
            logger.warning(f"Race not found: race_id={race_id}")
            return SPEED_INDEX_MEAN

        pedigree = (
            self.db.query(Pedigree).filter(Pedigree.horse_id == horse_id).first()
        )
        if pedigree is None:
            return SPEED_INDEX_MEAN

        return self._compute_score(pedigree, race)

    def calculate_batch(self, race_id: int) -> dict[int, float]:
        """レース全馬の血統指数を一括算出する。

        Args:
            race_id: DB の races.id

        Returns:
            {horse_id: pedigree_index} の dict。エントリなし時は空 dict。
        """
        race = self.db.query(Race).filter(Race.id == race_id).first()
        if not race:
            logger.warning(f"Race not found: race_id={race_id}")
            return {}

        entries = (
            self.db.query(RaceEntry).filter(RaceEntry.race_id == race_id).all()
        )
        if not entries:
            return {}

        # N+1 回避: 対象 horse_id の pedigree を一括取得
        horse_ids = [e.horse_id for e in entries]
        pedigrees = (
            self.db.query(Pedigree)
            .filter(Pedigree.horse_id.in_(horse_ids))
            .all()
        )
        ped_map: dict[int, Pedigree] = {p.horse_id: p for p in pedigrees}

        result: dict[int, float] = {}
        for entry in entries:
            ped = ped_map.get(entry.horse_id)
            if ped is None:
                result[entry.horse_id] = SPEED_INDEX_MEAN
            else:
                result[entry.horse_id] = self._compute_score(ped, race)

        return result

    # ------------------------------------------------------------------
    # 内部メソッド
    # ------------------------------------------------------------------

    def _compute_score(self, pedigree: Pedigree, race: Race) -> float:
        """血統 × レース条件のスコアを算出する。

        Args:
            pedigree: Pedigree ORM オブジェクト
            race: Race ORM オブジェクト

        Returns:
            血統指数（0-100）
        """
        surf = _surface_key(race.surface)
        dist_cat = _dist_category(race.distance)

        sire_score = _sire_line_score(pedigree.sire_line, surf, dist_cat)
        dam_sire_score = _sire_line_score(pedigree.dam_sire_line, surf, dist_cat)

        score = sire_score * SIRE_WEIGHT + dam_sire_score * DAM_SIRE_WEIGHT
        return round(max(INDEX_MIN, min(INDEX_MAX, score)), 1)
