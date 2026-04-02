"""コース適性指数算出Agent

馬の過去成績と競馬場コース特徴（直線距離・高低差・回り方向・芝種別）を組み合わせ、
対象コースへの適性をスコア化する。

算出ロジック:
  1. 馬の過去 LOOKBACK_RACES 戦の成績を取得（異常レース除外）
  2. 各過去レースに対して、対象コースとの「コース類似度」を算出
     - 回り方向一致: 0.25
     - 直線距離の近さ: 0.35
     - 高低差の近さ: 0.25
     - 1周距離の近さ: 0.15
     ※ 馬場種別(芝/ダ)が異なる場合は除外
  3. 距離近接度を乗じた最終重みを計算
     - |距離差| ≤ 200m: ×1.0
     - |距離差| ≤ 400m: ×0.7
     - |距離差| ≤ 600m: ×0.4
     - 600m超: ×0.1
  4. 加重平均スコアを算出（着順スコア + タイム偏差スコア）
  5. 信頼度加重でデフォルト50.0と合成
     - reliability = min(1.0, 有効重み合計 / RELIABLE_WEIGHT)
     - 最終スコア = reliability × 算出スコア + (1 - reliability) × 50.0
"""

from __future__ import annotations

import logging
from collections import defaultdict
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Race, RacecourseFeatures, RaceEntry, RaceResult
from ..utils.constants import SPEED_INDEX_MEAN, SPEED_INDEX_STD
from .base import IndexCalculator

logger = logging.getLogger(__name__)

# 過去何戦を参照するか
LOOKBACK_RACES = 20
# 距離近接度ブレーク点 (m)
DIST_BREAKS = [(200, 1.0), (400, 0.7), (600, 0.4)]
DIST_FALLBACK_WEIGHT = 0.1  # 600m超の重みスケール
# 信頼度計算：有効重み合計がこれ以上で reliability=1.0
RELIABLE_WEIGHT = 3.0
# 有効サンプル（重み付き）の最低件数：これ未満ならデフォルト値を返す
MIN_SAMPLE = 3

# コース類似度計算の次元ウェイト
SIM_W_DIRECTION = 0.25
SIM_W_STRAIGHT = 0.35
SIM_W_ELEVATION = 0.25
SIM_W_CIRCUIT = 0.15

# 同一馬場種別かどうかで類似度補正
GRASS_SAME_BONUS = 1.0
GRASS_DIFF_PENALTY = 0.6  # 洋芝 ↔ 野芝+洋芝 は完全に別扱いではないが割引


# 着順→生スコアの変換（1着=100, 以降-15ずつ, 最低0）
def _position_score(pos: int) -> float:
    return max(0.0, 100.0 - (pos - 1) * 15.0)


INDEX_MIN = 0.0
INDEX_MAX = 100.0


class CourseAptitudeCalculator(IndexCalculator):
    """コース適性指数算出Agent。

    競馬場コース特徴マスタ (racecourse_features) を用いて
    コース間の類似度を算出し、データ不足時も類似コースの成績で補完する。
    信頼度加重によりデータが少ない馬はデフォルト値50.0に近づける。
    """

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(db)
        self._std_time_cache: dict[tuple[str, int, str], tuple[float, float]] = {}
        # 競馬場特徴を初回アクセス時にキャッシュ（セッション非依存の SimpleNamespace で保持）
        self._course_features: dict[str, SimpleNamespace] | None = None

    # ------------------------------------------------------------------
    # 公開インターフェース
    # ------------------------------------------------------------------

    async def calculate(self, race_id: int, horse_id: int) -> float:
        race_result = await self.db.execute(select(Race).where(Race.id == race_id))
        race = race_result.scalar_one_or_none()
        if not race:
            logger.warning(f"Race not found: race_id={race_id}")
            return SPEED_INDEX_MEAN
        rows = await self._get_past_results_for_horse(
            horse_id, race.date, race_id,
            target_surface=race.surface or "", target_course=race.course, target_dist=int(race.distance or 0)
        )
        return self._compute_aptitude_index(rows, race)

    async def calculate_batch(self, race_id: int) -> dict[int, float]:
        race_result = await self.db.execute(select(Race).where(Race.id == race_id))
        race = race_result.scalar_one_or_none()
        if not race:
            logger.warning(f"Race not found: race_id={race_id}")
            return {}

        entries_result = await self.db.execute(select(RaceEntry).where(RaceEntry.race_id == race_id))
        entries = entries_result.scalars().all()
        if not entries:
            return {}

        horse_ids = [e.horse_id for e in entries]
        rows_map = await self._get_past_results_batch(
            horse_ids, race.date, race_id,
            target_surface=race.surface or "", target_course=race.course, target_dist=int(race.distance or 0)
        )

        return {
            entry.horse_id: self._compute_aptitude_index(rows_map.get(entry.horse_id, []), race)
            for entry in entries
        }

    # ------------------------------------------------------------------
    # コース特徴・類似度
    # ------------------------------------------------------------------

    async def _load_course_features(self) -> dict[str, SimpleNamespace]:
        """racecourse_features をセッション非依存のオブジェクトとしてキャッシュして返す。

        expunge_all() 後もデタッチされないよう ORM インスタンスを保持しない。
        """
        if self._course_features is None:
            result = await self.db.execute(select(RacecourseFeatures))
            rows = result.scalars().all()
            self._course_features = {
                r.course_code: SimpleNamespace(
                    direction=r.direction,
                    straight_distance=r.straight_distance,
                    elevation_diff=r.elevation_diff,
                    circuit_length=r.circuit_length,
                    grass_type=r.grass_type,
                )
                for r in rows
            }
        return self._course_features

    def _course_similarity(self, code_a: str, code_b: str) -> float:
        """2競馬場間の類似度スコア（0.0〜1.0）を返す。

        コース特徴が不明な場合は 0.5（中立）を返す。
        同一コードの場合は 1.0。

        類似度の計算軸:
          - 回り方向: 一致=1点, 不一致=0点  (weight SIM_W_DIRECTION)
          - 直線距離: 差をコース間最大差で正規化 (weight SIM_W_STRAIGHT)
          - 高低差: 同上 (weight SIM_W_ELEVATION)
          - 1周距離: 同上 (weight SIM_W_CIRCUIT)
        """
        if code_a == code_b:
            return 1.0

        # NOTE: _course_similarity は同期メソッドのため、呼び出し前に _course_features がロード済みであること
        features = self._course_features or {}
        fa = features.get(code_a)
        fb = features.get(code_b)
        if fa is None or fb is None:
            return 0.0  # 特徴不明→類似度なし（除外）

        # 回り方向
        dir_score = 1.0 if fa.direction == fb.direction else 0.0

        # 直線距離（正規化: 最大差 ≈ 658-262 = 396m を1.0とする）
        straight_range = 400.0
        straight_score = max(
            0.0,
            1.0 - abs(float(fa.straight_distance) - float(fb.straight_distance)) / straight_range,
        )

        # 高低差（正規化: 最大差 ≈ 3.5m を1.0とする）
        elevation_range = 4.0
        elevation_score = max(
            0.0, 1.0 - abs(float(fa.elevation_diff) - float(fb.elevation_diff)) / elevation_range
        )

        # 1周距離（正規化: 最大差 ≈ 2223-1600 = 623m を1.0とする）
        circuit_range = 700.0
        circuit_score = max(
            0.0, 1.0 - abs(float(fa.circuit_length) - float(fb.circuit_length)) / circuit_range
        )

        similarity = (
            dir_score * SIM_W_DIRECTION
            + straight_score * SIM_W_STRAIGHT
            + elevation_score * SIM_W_ELEVATION
            + circuit_score * SIM_W_CIRCUIT
        )

        # 芝種別補正（洋芝 vs 野芝+洋芝 は若干割引）
        if fa.grass_type != fb.grass_type:
            similarity *= GRASS_DIFF_PENALTY

        return round(similarity, 4)

    @staticmethod
    def _distance_proximity(dist_diff: int) -> float:
        """距離差(m)から近接度スケール係数を返す。"""
        abs_diff = abs(dist_diff)
        for threshold, scale in DIST_BREAKS:
            if abs_diff <= threshold:
                return scale
        return DIST_FALLBACK_WEIGHT

    # ------------------------------------------------------------------
    # データ取得
    # ------------------------------------------------------------------

    async def _get_past_results_for_horse(
        self,
        horse_id: int,
        before_date: str,
        exclude_race_id: int,
        target_surface: str = "",
        target_course: str = "",
        target_dist: int = 0,
    ) -> list[Any]:
        # コース特徴を事前ロード（_course_similarity で使用）
        await self._load_course_features()
        # 基準タイム事前ロード
        if target_course and target_dist and target_surface:
            await self._preload_standard_time(target_course, target_dist, target_surface)
        stmt = (
            select(RaceResult, Race)
            .join(Race, RaceResult.race_id == Race.id)
            .where(
                RaceResult.horse_id == horse_id,
                Race.date < before_date,
                RaceResult.race_id != exclude_race_id,
                RaceResult.finish_position.isnot(None),
                RaceResult.abnormality_code == 0,
            )
            .order_by(Race.date.desc())
            .limit(LOOKBACK_RACES)
        )
        result = await self.db.execute(stmt)
        return list(result.all())

    async def _get_past_results_batch(
        self,
        horse_ids: list[int],
        before_date: str,
        exclude_race_id: int,
        target_surface: str = "",
        target_course: str = "",
        target_dist: int = 0,
    ) -> dict[int, list[Any]]:
        if not horse_ids:
            return {}

        # コース特徴を事前ロード（_course_similarity で使用）
        await self._load_course_features()
        # 基準タイム事前ロード
        if target_course and target_dist and target_surface:
            await self._preload_standard_time(target_course, target_dist, target_surface)

        stmt = (
            select(RaceResult, Race)
            .join(Race, RaceResult.race_id == Race.id)
            .where(
                RaceResult.horse_id.in_(horse_ids),
                Race.date < before_date,
                RaceResult.race_id != exclude_race_id,
                RaceResult.finish_position.isnot(None),
                RaceResult.abnormality_code == 0,
            )
            .order_by(RaceResult.horse_id, Race.date.desc())
        )
        result = await self.db.execute(stmt)
        rows = result.all()

        result_map: dict[int, list[Any]] = defaultdict(list)
        count_map: dict[int, int] = defaultdict(int)
        for row in rows:
            hid = row.RaceResult.horse_id
            if count_map[hid] < LOOKBACK_RACES:
                result_map[hid].append(row)
                count_map[hid] += 1

        return dict(result_map)

    # ------------------------------------------------------------------
    # 指数算出
    # ------------------------------------------------------------------

    def _compute_aptitude_index(self, rows: list[Any], target_race: Race) -> float:
        """過去レース結果とコース類似度から適性指数を算出する。

        手順:
          1. 各過去レースの重み = course_similarity × distance_proximity
             ※ 馬場種別(芝/ダ/障)が異なる場合は weight=0 でスキップ
          2. 加重平均スコアを算出
          3. 信頼度 = min(1.0, 有効重み合計 / RELIABLE_WEIGHT)
          4. 最終スコア = reliability × raw + (1 - reliability) × 50.0
        """
        if not rows:
            return SPEED_INDEX_MEAN

        target_course = target_race.course
        target_dist = int(target_race.distance or 0)
        target_surface = target_race.surface or ""

        weighted_scores: list[tuple[float, float]] = []

        for row in rows:
            result: RaceResult = row.RaceResult
            race: Race = row.Race

            if result.finish_position is None:
                continue

            past_surface = race.surface or ""

            # 馬場種別が異なれば除外（芝 vs ダート は全く別の適性）
            if past_surface != target_surface:
                continue

            # コース類似度
            sim = self._course_similarity(race.course, target_course)

            # 距離近接度
            past_dist = int(race.distance or 0)
            dist_prox = self._distance_proximity(past_dist - target_dist)

            weight = sim * dist_prox
            if weight < 0.05:
                continue  # 実質ゼロの寄与は除外

            # スコア算出（着順スコア + タイム偏差スコアの合成）
            pos_score = _position_score(int(result.finish_position))
            time_score = self._compute_time_score(result, race)
            score = pos_score * 0.6 + (time_score if time_score is not None else pos_score) * 0.4

            weighted_scores.append((score, weight))

        if len(weighted_scores) < MIN_SAMPLE:
            return SPEED_INDEX_MEAN

        total_w = sum(w for _, w in weighted_scores)
        raw_score = sum(s * w for s, w in weighted_scores) / total_w

        # 信頼度加重：データ不足はデフォルト値50.0に引き寄せる
        reliability = min(1.0, total_w / RELIABLE_WEIGHT)
        final = reliability * raw_score + (1.0 - reliability) * SPEED_INDEX_MEAN

        return round(max(INDEX_MIN, min(INDEX_MAX, final)), 1)

    # ------------------------------------------------------------------
    # タイム偏差スコア
    # ------------------------------------------------------------------

    def _compute_time_score(self, result: RaceResult, race: Race) -> float | None:
        """同コース・距離・馬場の基準タイムとの偏差を0-100スコアに変換する。"""
        if result.finish_time is None:
            return None

        std_time, std_dev = self._get_standard_time(
            race.course, int(race.distance or 0), race.surface or ""
        )
        if std_dev < 0.01:
            return None

        diff = std_time - float(result.finish_time)
        score = (diff / std_dev) * SPEED_INDEX_STD + SPEED_INDEX_MEAN
        return max(INDEX_MIN, min(INDEX_MAX, score))

    def _get_standard_time(self, course: str, distance: int, surface: str) -> tuple[float, float]:
        """コース・距離・馬場の基準タイム（平均・標準偏差）をキャッシュから返す。

        NOTE: このメソッドはキャッシュのみ参照する。初回ロードには _preload_standard_times を使用すること。
        """
        cache_key = (course, distance, surface)
        return self._std_time_cache.get(cache_key, (0.0, 0.0))

    async def _preload_standard_time(self, course: str, distance: int, surface: str) -> None:
        """単一条件の基準タイムをDBから取得してキャッシュする。"""
        cache_key = (course, distance, surface)
        if cache_key in self._std_time_cache:
            return

        stmt = (
            select(
                func.avg(RaceResult.finish_time).label("avg_time"),
                func.stddev_pop(RaceResult.finish_time).label("std_time"),
                func.count(RaceResult.id).label("cnt"),
            )
            .join(Race, RaceResult.race_id == Race.id)
            .where(
                Race.course == course,
                Race.distance == distance,
                Race.surface == surface,
                RaceResult.finish_time.isnot(None),
                RaceResult.abnormality_code == 0,
            )
        )
        result = await self.db.execute(stmt)
        row = result.first()

        if row is None or row.cnt is None or int(row.cnt) < 5:
            self._std_time_cache[cache_key] = (0.0, 0.0)
            return

        avg = float(row.avg_time) if row.avg_time else 0.0
        std = float(row.std_time) if row.std_time else 0.0
        self._std_time_cache[cache_key] = (avg, max(std, 0.01))
