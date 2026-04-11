"""距離変更適性指数算出Agent（Distance Change Index）

前走からの距離変更パターン（延長/短縮/同距離）に対する
馬個別の成績傾向をスコア化する。

算出ロジック:
  1. 現在レースの距離を取得
  2. 各馬の直前レース距離を取得
  3. 距離変更を分類:
       延長（extension）: curr_dist - prev_dist >= 200m
       短縮（shortening）: curr_dist - prev_dist <= -200m
       同距離（same）: ±200m 未満
  4. 過去の全レースペアから同一パターンの成績を集計:
       連続するレースペア (race_i, race_{i-1}) で距離変更を判定
       同じ変更パターンの勝利数・出走数をカウント
  5. 「同距離」は常に 50.0 を返す（方向性なし）
  6. total_in_pattern >= 3 の場合:
       ratio = pattern_win_rate / max(overall_win_rate, 0.01)
       score = 50 + clip((ratio - 1.0) × 30, -25, +25)
  7. データ不足時は 50.0
"""

from __future__ import annotations

import logging
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Race, RaceEntry, RaceResult
from .base import IndexCalculator

logger = logging.getLogger(__name__)

# 延長/短縮の閾値（m）
CHANGE_THRESHOLD = 200
# パターン統計に必要な最小出走数
MIN_PATTERN_RACES = 3
# デフォルトスコア（中立）
DEFAULT_SCORE = 50.0
# スコア変換係数
RATIO_SCALE = 30.0
# クリップ範囲
CLIP_MIN = -25.0
CLIP_MAX = 25.0

# 距離変更パターン
_PATTERN_EXTENSION = "extension"
_PATTERN_SHORTENING = "shortening"
_PATTERN_SAME = "same"


def _classify_change(curr_dist: int, prev_dist: int) -> str:
    """距離変更パターンを分類する。

    Args:
        curr_dist: 今回レースの距離（m）
        prev_dist: 前走の距離（m）

    Returns:
        パターン文字列 "extension" / "shortening" / "same"
    """
    diff = curr_dist - prev_dist
    if diff >= CHANGE_THRESHOLD:
        return _PATTERN_EXTENSION
    if diff <= -CHANGE_THRESHOLD:
        return _PATTERN_SHORTENING
    return _PATTERN_SAME


class DistanceChangeIndexCalculator(IndexCalculator):
    """距離変更適性指数算出Agent。

    各馬の延長・短縮・同距離それぞれのパターンにおける
    過去成績の傾向をスコア化する。データが不足する場合は
    中立値（50.0）を返す。
    """

    def __init__(self, db: AsyncSession) -> None:
        """初期化。

        Args:
            db: SQLAlchemy 非同期セッション
        """
        super().__init__(db)

    # ------------------------------------------------------------------
    # 公開インターフェース
    # ------------------------------------------------------------------

    async def calculate(self, race_id: int, horse_id: int) -> float:
        """単一馬の距離変更適性指数を算出する。

        Args:
            race_id: DB の races.id（対象レース）
            horse_id: DB の horses.id

        Returns:
            距離変更適性指数（0-100, 中立=50）。データ不足時は DEFAULT_SCORE。
        """
        race_result = await self.db.execute(select(Race).where(Race.id == race_id))
        race = race_result.scalar_one_or_none()
        if not race:
            return DEFAULT_SCORE

        batch = await self._compute_batch([horse_id], race)
        return batch.get(horse_id, DEFAULT_SCORE)

    async def calculate_batch(self, race_id: int) -> dict[int, float]:
        """レース全馬の距離変更適性指数を一括算出する。

        N+1 を回避するため、全馬のデータを単一または少数のクエリで取得する。

        Args:
            race_id: DB の races.id

        Returns:
            {horse_id: distance_change_index} の dict。
        """
        race_result = await self.db.execute(select(Race).where(Race.id == race_id))
        race = race_result.scalar_one_or_none()
        if not race:
            return {}

        entries_result = await self.db.execute(
            select(RaceEntry).where(RaceEntry.race_id == race_id)
        )
        entries = entries_result.scalars().all()
        if not entries:
            return {}

        horse_ids = [e.horse_id for e in entries]
        return await self._compute_batch(horse_ids, race)

    # ------------------------------------------------------------------
    # 内部メソッド
    # ------------------------------------------------------------------

    async def _compute_batch(
        self,
        horse_ids: list[int],
        current_race: Race,
    ) -> dict[int, float]:
        """複数馬の距離変更適性指数を一括算出する。

        Args:
            horse_ids: 対象馬 ID リスト
            current_race: 現在の Race オブジェクト

        Returns:
            {horse_id: distance_change_index} の dict。
        """
        if not horse_ids:
            return {}

        curr_dist = current_race.distance
        before_date = current_race.date
        exclude_race_id = current_race.id

        # ----------------------------------------------------------------
        # Step 1: 全馬の過去レース成績を一括取得（日付降順）
        # ----------------------------------------------------------------
        stmt = (
            select(
                RaceResult.horse_id,
                RaceResult.finish_position,
                RaceResult.abnormality_code,
                Race.date,
                Race.distance,
            )
            .join(Race, RaceResult.race_id == Race.id)
            .where(
                RaceResult.horse_id.in_(horse_ids),
                Race.date < before_date,
                RaceResult.race_id != exclude_race_id,
                RaceResult.finish_position.is_not(None),
            )
            .order_by(RaceResult.horse_id, Race.date.desc())
        )
        rows = (await self.db.execute(stmt)).all()

        # horse_id -> [(date, distance, finish_position, abnormality_code), ...] 新しい順
        horse_races: dict[int, list[tuple[str, int, int, int]]] = defaultdict(list)
        for row in rows:
            horse_races[row.horse_id].append(
                (
                    row.date,
                    int(row.distance),
                    int(row.finish_position),
                    int(row.abnormality_code) if row.abnormality_code is not None else 0,
                )
            )

        # ----------------------------------------------------------------
        # Step 2: 各馬のスコアを算出
        # ----------------------------------------------------------------
        result: dict[int, float] = {}
        for hid in horse_ids:
            past = horse_races.get(hid, [])

            if not past:
                result[hid] = DEFAULT_SCORE
                continue

            # 現在レースと直前レースの距離変更を判定
            prev_race_dist = past[0][1]  # 最新（直前）の距離
            current_pattern = _classify_change(curr_dist, prev_race_dist)

            # 同距離は中立
            if current_pattern == _PATTERN_SAME:
                result[hid] = DEFAULT_SCORE
                continue

            # ----------------------------------------------------------------
            # Step 3: 過去レースペアでパターン別成績を集計
            # ----------------------------------------------------------------
            pattern_wins = 0
            pattern_total = 0
            total_wins = 0
            total_valid = 0

            # valid = abnormality_code == 0 のレースのみ
            valid_races = [(d, dist, fp) for d, dist, fp, abn in past if abn == 0]
            total_valid = len(valid_races)
            total_wins = sum(1 for _, _, fp in valid_races if fp == 1)

            # 連続ペア (race_i, race_{i+1}) で変更パターンを判定
            # past[0] が最新。ペアは (past[i], past[i+1]) で past[i] が今回、past[i+1] が前回
            for i in range(len(past) - 1):
                curr_d, curr_dist_i, curr_fp, curr_abn = past[i]
                prev_d, prev_dist_i, prev_fp, prev_abn = past[i + 1]

                # 今回のレース(i)の異常チェック
                if curr_abn != 0:
                    continue
                if curr_fp is None:
                    continue

                pattern_i = _classify_change(curr_dist_i, prev_dist_i)
                if pattern_i == current_pattern:
                    pattern_total += 1
                    if curr_fp == 1:
                        pattern_wins += 1

            if pattern_total < MIN_PATTERN_RACES:
                result[hid] = DEFAULT_SCORE
                continue

            overall_win_rate = total_wins / max(total_valid, 1)
            pattern_win_rate = pattern_wins / pattern_total
            ratio = pattern_win_rate / max(overall_win_rate, 0.01)
            raw = (ratio - 1.0) * RATIO_SCALE
            score = 50.0 + max(CLIP_MIN, min(CLIP_MAX, raw))
            result[hid] = round(score, 1)

        return result
