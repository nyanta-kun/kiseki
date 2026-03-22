"""コース適性指数算出Agent

馬の過去成績をコース・距離・馬場(surface)別に集計し、
対象コースへの適性をスコア化する。

算出ロジック:
  1. 馬の過去 LOOKBACK_RACES 戦の成績を取得（異常レース除外）
  2. 各レースを完全一致/距離近似/馬場一致の3カテゴリで重み付け
     - 完全一致 (同コース・同距離・同馬場): 重み 1.0
     - 距離近似 (同コース・±DIST_TOLERANCE m以内・同馬場): 重み 0.6
     - 馬場一致 (同コース・同馬場のみ): 重み 0.3
  3. カテゴリごとに「平均着順スコア」と「タイム偏差スコア」を計算
     - 着順スコア: 1着=100, 2着=80, 3着=65, 4着=50 ... 1着毎に-15
     - タイム偏差: 同条件の標準タイムとの差を正規化
  4. 加重平均して生スコアを算出
  5. 平均50, σ=10 に正規化（他指数と同スケール）
  6. データ不足（MIN_SAMPLE 未満）は SPEED_INDEX_MEAN=50 を返す
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db.models import Race, RaceEntry, RaceResult
from ..utils.constants import SPEED_INDEX_MEAN, SPEED_INDEX_STD
from .base import IndexCalculator

logger = logging.getLogger(__name__)

# 過去何戦を参照するか
LOOKBACK_RACES = 15
# 距離近似の許容範囲 (m)
DIST_TOLERANCE = 200
# データ不足判定の最低サンプル数
MIN_SAMPLE = 3
# 完全一致/距離近似/馬場一致の重み
WEIGHT_EXACT = 1.0
WEIGHT_DIST = 0.6
WEIGHT_SURFACE = 0.3
# 着順→生スコアの変換テーブル (1着=100, 以降-15ずつ, 最低0)
def _position_score(pos: int) -> float:
    """着順を0-100のスコアに変換する。"""
    return max(0.0, 100.0 - (pos - 1) * 15.0)

# 指数クリップ
INDEX_MIN = 0.0
INDEX_MAX = 100.0


class CourseAptitudeCalculator(IndexCalculator):
    """コース適性指数算出Agent。

    IndexCalculator を継承し、単一馬（calculate）と
    レース全馬バッチ（calculate_batch）の両インターフェースを提供する。
    """

    def __init__(self, db: Session) -> None:
        """初期化。

        Args:
            db: SQLAlchemy セッション
        """
        super().__init__(db)
        # 基準タイムのキャッシュ（コース・距離・馬場ごと）
        self._std_time_cache: dict[tuple[str, int, str], tuple[float, float]] = {}

    # ------------------------------------------------------------------
    # 公開インターフェース
    # ------------------------------------------------------------------

    def calculate(self, race_id: int, horse_id: int) -> float:
        """単一馬のコース適性指数を算出する。

        Args:
            race_id: DB の races.id（対象レース）
            horse_id: DB の horses.id

        Returns:
            コース適性指数（0-100, 平均50）。データ不足時は SPEED_INDEX_MEAN。
        """
        race = self.db.query(Race).filter(Race.id == race_id).first()
        if not race:
            logger.warning(f"Race not found: race_id={race_id}")
            return SPEED_INDEX_MEAN

        rows = self._get_past_results_for_horse(horse_id, race.date, race_id)
        return self._compute_aptitude_index(rows, race)

    def calculate_batch(self, race_id: int) -> dict[int, float]:
        """レース全馬のコース適性指数を一括算出する。

        Args:
            race_id: DB の races.id

        Returns:
            {horse_id: course_aptitude_index} のdict。エントリが存在しない場合は空dict。
        """
        race = self.db.query(Race).filter(Race.id == race_id).first()
        if not race:
            logger.warning(f"Race not found: race_id={race_id}")
            return {}

        entries = self.db.query(RaceEntry).filter(RaceEntry.race_id == race_id).all()
        if not entries:
            return {}

        horse_ids = [e.horse_id for e in entries]
        rows_map = self._get_past_results_batch(horse_ids, race.date, race_id)

        result: dict[int, float] = {}
        for entry in entries:
            rows = rows_map.get(entry.horse_id, [])
            result[entry.horse_id] = self._compute_aptitude_index(rows, race)

        return result

    # ------------------------------------------------------------------
    # 内部メソッド
    # ------------------------------------------------------------------

    def _get_past_results_for_horse(
        self, horse_id: int, before_date: str, exclude_race_id: int
    ) -> list[Any]:
        """単一馬の過去レース結果を取得する。

        Args:
            horse_id: horses.id
            before_date: この日付より前のレースのみ取得（YYYYMMDD）
            exclude_race_id: 当該レースは除外

        Returns:
            [(RaceResult, Race), ...]（日付降順, 最大 LOOKBACK_RACES 件）
        """
        return (
            self.db.query(RaceResult, Race)
            .join(Race, RaceResult.race_id == Race.id)
            .filter(
                RaceResult.horse_id == horse_id,
                Race.date < before_date,
                RaceResult.race_id != exclude_race_id,
                RaceResult.finish_position.isnot(None),
                RaceResult.abnormality_code == 0,
            )
            .order_by(Race.date.desc())
            .limit(LOOKBACK_RACES)
            .all()
        )

    def _get_past_results_batch(
        self, horse_ids: list[int], before_date: str, exclude_race_id: int
    ) -> dict[int, list[Any]]:
        """複数馬の過去レース結果を単一クエリで一括取得する。

        Args:
            horse_ids: 対象 horses.id のリスト
            before_date: この日付より前のレース（YYYYMMDD）
            exclude_race_id: 当該レースは除外

        Returns:
            {horse_id: [(RaceResult, Race), ...]}（各馬最大 LOOKBACK_RACES 件）
        """
        if not horse_ids:
            return {}

        rows = (
            self.db.query(RaceResult, Race)
            .join(Race, RaceResult.race_id == Race.id)
            .filter(
                RaceResult.horse_id.in_(horse_ids),
                Race.date < before_date,
                RaceResult.race_id != exclude_race_id,
                RaceResult.finish_position.isnot(None),
                RaceResult.abnormality_code == 0,
            )
            .order_by(RaceResult.horse_id, Race.date.desc())
            .all()
        )

        result_map: dict[int, list[Any]] = defaultdict(list)
        count_map: dict[int, int] = defaultdict(int)

        for row in rows:
            hid = row.RaceResult.horse_id
            if count_map[hid] < LOOKBACK_RACES:
                result_map[hid].append(row)
                count_map[hid] += 1

        return dict(result_map)

    def _compute_aptitude_index(self, rows: list[Any], target_race: Race) -> float:
        """過去レース結果からコース適性指数を算出する。

        Args:
            rows: [(RaceResult, Race), ...] 過去レース結果
            target_race: 対象レース（コース・距離・馬場の比較基準）

        Returns:
            コース適性指数（0-100, 平均50）。データ不足時は SPEED_INDEX_MEAN。
        """
        if not rows:
            return SPEED_INDEX_MEAN

        target_course = target_race.course
        target_dist = target_race.distance or 0
        target_surface = target_race.surface or ""

        weighted_scores: list[tuple[float, float]] = []  # (スコア, 重み)

        for row in rows:
            result: RaceResult = row.RaceResult
            race: Race = row.Race

            if result.finish_position is None:
                continue

            pos_score = _position_score(int(result.finish_position))

            # タイム偏差スコア（算出できない場合は着順スコアのみ）
            time_score = self._compute_time_score(result, race)
            combined = pos_score * 0.6 + (time_score if time_score is not None else pos_score) * 0.4

            # 重みカテゴリの判定（優先度の高い条件から順に評価）
            same_course = race.course == target_course
            same_surface = (race.surface or "") == target_surface
            dist = race.distance or 0
            within_tolerance = abs(dist - target_dist) <= DIST_TOLERANCE

            if not same_course:
                # コース不一致は除外
                continue
            elif within_tolerance and same_surface:
                # 完全一致: 同コース・距離近似・同馬場
                weight = WEIGHT_EXACT
            elif within_tolerance:
                # 同コース・距離近似（馬場不問）
                weight = WEIGHT_DIST
            elif same_surface:
                # 同コース・同馬場（距離は範囲外）
                weight = WEIGHT_SURFACE
            else:
                # 同コースのみ（距離・馬場ともに不一致）
                weight = WEIGHT_SURFACE * 0.5

            weighted_scores.append((combined, weight))

        if len(weighted_scores) < MIN_SAMPLE:
            return SPEED_INDEX_MEAN

        total_w = sum(w for _, w in weighted_scores)
        raw_score = sum(s * w for s, w in weighted_scores) / total_w

        # raw_score は概ね [0, 100] の範囲で、平均が50近辺になるよう設計
        # 着順スコア平均: 1着=100, 2着=85, ... 着順の期待値が4位程度なら約55
        # ここではそのまま使用し、[0,100]にクリップ
        return round(max(INDEX_MIN, min(INDEX_MAX, raw_score)), 1)

    def _compute_time_score(self, result: RaceResult, race: Race) -> float | None:
        """タイム偏差を0-100スコアに変換する。

        同コース・距離・馬場の基準タイムとの差を指数化する。

        Args:
            result: レース結果
            race: レース情報

        Returns:
            タイムスコア（0-100）。算出不可の場合は None。
        """
        if result.finish_time is None:
            return None

        course = race.course
        distance = race.distance or 0
        surface = race.surface or ""

        std_time, std_dev = self._get_standard_time(course, distance, surface)
        if std_dev < 0.01:
            return None

        actual_time = float(result.finish_time)
        diff = std_time - actual_time
        score = (diff / std_dev) * SPEED_INDEX_STD + SPEED_INDEX_MEAN
        return max(INDEX_MIN, min(INDEX_MAX, score))

    def _get_standard_time(
        self, course: str, distance: int, surface: str
    ) -> tuple[float, float]:
        """コース・距離・馬場の基準タイム（平均・標準偏差）を返す。

        同セッション内でキャッシュし、DBアクセスを最小化する。

        Args:
            course: 場コード
            distance: 距離（m）
            surface: 馬場種別（芝/ダ/障）

        Returns:
            (平均タイム秒, 標準偏差秒)。サンプル不足時は (0.0, 0.0)。
        """
        cache_key = (course, distance, surface)
        if cache_key in self._std_time_cache:
            return self._std_time_cache[cache_key]

        row = (
            self.db.query(
                func.avg(RaceResult.finish_time).label("avg_time"),
                func.stddev_pop(RaceResult.finish_time).label("std_time"),
                func.count(RaceResult.id).label("cnt"),
            )
            .join(Race, RaceResult.race_id == Race.id)
            .filter(
                Race.course == course,
                Race.distance == distance,
                Race.surface == surface,
                RaceResult.finish_time.isnot(None),
                RaceResult.abnormality_code == 0,
            )
            .first()
        )

        if row is None or row.cnt is None or int(row.cnt) < 5:
            self._std_time_cache[cache_key] = (0.0, 0.0)
            return (0.0, 0.0)

        avg = float(row.avg_time) if row.avg_time else 0.0
        std = float(row.std_time) if row.std_time else 0.0
        value = (avg, max(std, 0.01))
        self._std_time_cache[cache_key] = value
        return value
