"""スピード指数算出Agent

JRA-VAN SEレコードから取得した着タイム・上がり3Fをもとに、
同コース・距離・馬場状態の基準タイムとの差分を標準化してスピード指数を算出する。

算出ロジック:
  1. 馬の直近LOOKBACK_RACES戦の成績をバッチ取得
  2. 各レースで「斤量補正後タイム」を計算（実斤量と基準斤量55kgの差 × 0.5秒）
  3. 同条件（コース・距離・芝ダ・馬場状態）の基準タイム・標準偏差を算出
  4. (基準タイム - 補正後タイム) / 標準偏差 × 10 + 50 で指数化（平均50, σ=10）
  5. 直近レースほど重みを大きくして加重平均

制約:
  - 除外・取消（abnormality_code > 0）のレースは除外
  - 基準タイム算出に必要な最低サンプル数: MIN_STD_SAMPLE
  - サンプル不足時は平均値(50)を返す
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Race, RaceEntry, RaceResult
from ..utils.constants import (
    BASE_WEIGHT,
    SPEED_INDEX_MEAN,
    SPEED_INDEX_STD,
    WEIGHT_CORRECTION_PER_KG,
)
from .base import IndexCalculator

logger = logging.getLogger(__name__)

# 過去何戦を参照するか
LOOKBACK_RACES = 10
# 基準タイム算出に必要な最低サンプル数
MIN_STD_SAMPLE = 5
# 指数クリップ範囲
INDEX_MIN = 0.0
INDEX_MAX = 100.0
# 加重平均の減衰率（直近から遡るほど 0.8^n 倍）
WEIGHT_DECAY = 0.8


class SpeedIndexCalculator(IndexCalculator):
    """スピード指数算出Agent。

    IndexCalculator を継承し、単一馬（calculate）と
    レース全馬バッチ（calculate_batch）の両インターフェースを提供する。
    """

    def __init__(self, db: AsyncSession) -> None:
        """初期化。

        Args:
            db: SQLAlchemy 非同期セッション
        """
        super().__init__(db)
        # 基準タイムのメモリキャッシュ（同セッション内で再利用）
        self._std_time_cache: dict[tuple[str, int, str, str | None], tuple[float, float]] = {}

    # ------------------------------------------------------------------
    # 公開インターフェース
    # ------------------------------------------------------------------

    async def calculate(self, race_id: int, horse_id: int) -> float:
        """単一馬のスピード指数を算出する。

        Args:
            race_id: DB の races.id
            horse_id: DB の horses.id

        Returns:
            スピード指数（0-100, 平均50）。データ不足時は SPEED_INDEX_MEAN。
        """
        result = await self.db.execute(select(Race).where(Race.id == race_id))
        race = result.scalar_one_or_none()
        if not race:
            logger.warning(f"Race not found: race_id={race_id}")
            return SPEED_INDEX_MEAN

        rows = await self._get_past_results_for_horse(horse_id, race.date, race_id)
        scores = self._compute_scores(rows)
        return self._weighted_average(scores)

    async def calculate_batch(self, race_id: int) -> dict[int, float]:
        """レース全馬のスピード指数を一括算出する。

        N+1 を回避するため、全馬の過去レース結果を単一クエリで取得する。

        Args:
            race_id: DB の races.id

        Returns:
            {horse_id: speed_index} のdict。エントリが存在しない場合は空dict。
        """
        race_result = await self.db.execute(select(Race).where(Race.id == race_id))
        race = race_result.scalar_one_or_none()
        if not race:
            logger.warning(f"Race not found: race_id={race_id}")
            return {}

        entries_result = await self.db.execute(
            select(RaceEntry).where(RaceEntry.race_id == race_id)
        )
        entries = entries_result.scalars().all()
        if not entries:
            return {}

        horse_ids = [e.horse_id for e in entries]

        # 全馬の過去結果を並行（単一クエリ）で取得
        rows_map = await self._get_past_results_batch(horse_ids, race.date, race_id)

        result: dict[int, float] = {}
        for entry in entries:
            rows = rows_map.get(entry.horse_id, [])
            scores = self._compute_scores(rows)
            result[entry.horse_id] = self._weighted_average(scores)

        return result

    # ------------------------------------------------------------------
    # 内部メソッド
    # ------------------------------------------------------------------

    async def _get_past_results_for_horse(
        self, horse_id: int, before_date: str, exclude_race_id: int
    ) -> list[Any]:
        """単一馬の過去レース結果を取得する。

        Args:
            horse_id: horses.id
            before_date: この日付より前のレースのみ取得（YYYYMMDD）
            exclude_race_id: 当該レースは除外

        Returns:
            [(RaceResult, Race, RaceEntry), ...]（日付降順, 最大 LOOKBACK_RACES 件）
        """
        stmt = (
            select(RaceResult, Race, RaceEntry)
            .join(Race, RaceResult.race_id == Race.id)
            .join(
                RaceEntry,
                and_(
                    RaceEntry.race_id == RaceResult.race_id,
                    RaceEntry.horse_id == RaceResult.horse_id,
                ),
            )
            .where(
                RaceResult.horse_id == horse_id,
                Race.date < before_date,
                RaceResult.race_id != exclude_race_id,
                RaceResult.finish_time.isnot(None),
                RaceResult.abnormality_code == 0,
            )
            .order_by(Race.date.desc())
            .limit(LOOKBACK_RACES)
        )
        result = await self.db.execute(stmt)
        return result.all()

    async def _get_past_results_batch(
        self, horse_ids: list[int], before_date: str, exclude_race_id: int
    ) -> dict[int, list[Any]]:
        """複数馬の過去レース結果を単一クエリで一括取得する。

        Args:
            horse_ids: 対象 horses.id のリスト
            before_date: この日付より前のレース（YYYYMMDD）
            exclude_race_id: 当該レースは除外

        Returns:
            {horse_id: [(RaceResult, Race, RaceEntry), ...]}（各馬最大 LOOKBACK_RACES 件）
        """
        if not horse_ids:
            return {}

        stmt = (
            select(RaceResult, Race, RaceEntry)
            .join(Race, RaceResult.race_id == Race.id)
            .join(
                RaceEntry,
                and_(
                    RaceEntry.race_id == RaceResult.race_id,
                    RaceEntry.horse_id == RaceResult.horse_id,
                ),
            )
            .where(
                RaceResult.horse_id.in_(horse_ids),
                Race.date < before_date,
                RaceResult.race_id != exclude_race_id,
                RaceResult.finish_time.isnot(None),
                RaceResult.abnormality_code == 0,
            )
            .order_by(RaceResult.horse_id, Race.date.desc())
        )
        result = await self.db.execute(stmt)
        rows = result.all()

        # horse_id ごとにグループ化し、最新 LOOKBACK_RACES 件を保持
        result_map: dict[int, list[Any]] = defaultdict(list)
        count_map: dict[int, int] = defaultdict(int)

        for row in rows:
            hid = row.RaceResult.horse_id
            if count_map[hid] < LOOKBACK_RACES:
                result_map[hid].append(row)
                count_map[hid] += 1

        return dict(result_map)

    def _compute_scores(self, rows: list[Any]) -> list[float]:
        """過去レース結果リストからスピードスコアリストを算出する。

        Args:
            rows: [(RaceResult, Race, RaceEntry), ...]

        Returns:
            各レースのスピードスコアリスト（算出不可なレースは除外）
        """
        scores = []
        for row in rows:
            s = self._single_race_speed_score(row.RaceResult, row.Race, row.RaceEntry)
            if s is not None:
                scores.append(s)
        return scores

    def _single_race_speed_score(
        self, result: RaceResult, race: Race, entry: RaceEntry
    ) -> float | None:
        """1レース分のスピードスコアを算出する。

        Args:
            result: RaceResult インスタンス
            race: Race インスタンス
            entry: RaceEntry インスタンス

        Returns:
            スピードスコア（0-100）、算出不可の場合は None
        """
        if result.finish_time is None or result.finish_position is None:
            return None
        if result.abnormality_code and result.abnormality_code > 0:
            return None

        actual_time = float(result.finish_time)  # 秒単位

        # 斤量補正: (実斤量 - 基準斤量) × 0.5秒をタイムに加算して基準斤量換算へ
        weight = float(entry.weight_carried) if entry.weight_carried else BASE_WEIGHT
        weight_correction = (weight - BASE_WEIGHT) * WEIGHT_CORRECTION_PER_KG
        adjusted_time = actual_time + weight_correction

        # 同条件の基準タイム・標準偏差を取得（キャッシュはここでは使えないためNoneを返す）
        # NOTE: 非同期メソッドのため _get_standard_time は呼べない。呼び出し元で事前キャッシュ済みを使う
        std_time, std_dev = self._std_time_cache.get(
            (race.course, race.distance or 0, race.surface or "", race.condition),
            (0.0, 0.0),
        )

        if std_dev < 0.01:
            # 分散ゼロ（サンプル不足など）は指数化できない
            return None

        # スピード指数: 基準より速いほど高スコア
        diff = std_time - adjusted_time
        score = (diff / std_dev) * SPEED_INDEX_STD + SPEED_INDEX_MEAN

        return max(INDEX_MIN, min(INDEX_MAX, score))

    async def _get_standard_time(
        self, course: str, distance: int, surface: str, condition: str | None
    ) -> tuple[float, float]:
        """コース・距離・芝ダ・馬場状態別の基準タイムと標準偏差を返す。

        同セッション内でキャッシュし、同一条件で2回以上呼ばれてもDBアクセスは1回。

        Args:
            course: 場コード（例: "05" = 東京）
            distance: 距離（m）
            surface: 芝/ダ/障
            condition: 良/稍/重/不（None は条件不問で集計）

        Returns:
            (平均タイム秒, 標準偏差秒)。サンプル不足時は (0.0, 0.0)。
        """
        cache_key = (course, distance, surface, condition)
        if cache_key in self._std_time_cache:
            return self._std_time_cache[cache_key]

        value = await self._compute_standard_time(course, distance, surface, condition)
        self._std_time_cache[cache_key] = value
        return value

    async def _compute_standard_time(
        self, course: str, distance: int, surface: str, condition: str | None
    ) -> tuple[float, float]:
        """DB から基準タイムを算出する。

        同コース・距離・芝ダ・馬場状態の全着順（異常なし）の
        平均・標準偏差を使用する。

        Args:
            course: 場コード
            distance: 距離（m）
            surface: 芝/ダ/障
            condition: 馬場状態（None の場合は条件不問）

        Returns:
            (平均秒, 標準偏差秒)。サンプル MIN_STD_SAMPLE 未満は (0.0, 0.0)。
        """
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

        if condition is not None:
            stmt = stmt.where(Race.condition == condition)

        result = await self.db.execute(stmt)
        row = result.first()

        if row is None or row.cnt is None or int(row.cnt) < MIN_STD_SAMPLE:
            logger.debug(
                f"標準タイムのサンプル不足: course={course}, dist={distance}, "
                f"surface={surface}, cond={condition}, cnt={row.cnt if row else 0}"
            )
            return (0.0, 0.0)

        avg = float(row.avg_time) if row.avg_time else 0.0
        std = float(row.std_time) if row.std_time else 0.0

        return (avg, max(std, 0.01))  # 標準偏差は最低 0.01 秒

    async def _preload_standard_times(self, race_id: int) -> None:
        """レース内の全馬が使う基準タイムを事前に一括キャッシュする。

        calculate_batch の前に呼び出すことで、_single_race_speed_score での
        キャッシュ参照が正しく機能するようにする。

        Args:
            race_id: DB の races.id（このレースの出走馬の過去レース条件を収集）
        """
        # 対象レースの過去結果に登場するコース・距離・芝ダ・馬場の組み合わせを取得
        entries_result = await self.db.execute(
            select(RaceEntry).where(RaceEntry.race_id == race_id)
        )
        entries = entries_result.scalars().all()
        horse_ids = [e.horse_id for e in entries]

        if not horse_ids:
            return

        race_result = await self.db.execute(select(Race).where(Race.id == race_id))
        race = race_result.scalar_one_or_none()
        if not race:
            return

        # 過去結果のコース・距離・表面・馬場の組み合わせを収集
        past_stmt = (
            select(Race.course, Race.distance, Race.surface, Race.condition)
            .join(RaceResult, RaceResult.race_id == Race.id)
            .where(
                RaceResult.horse_id.in_(horse_ids),
                Race.date < race.date,
                RaceResult.race_id != race_id,
                RaceResult.finish_time.isnot(None),
                RaceResult.abnormality_code == 0,
            )
            .distinct()
        )
        conditions_result = await self.db.execute(past_stmt)
        combos = conditions_result.all()

        for c_course, c_distance, c_surface, c_condition in combos:
            if c_course and c_distance and c_surface:
                await self._get_standard_time(c_course, c_distance or 0, c_surface or "", c_condition)

    @staticmethod
    def _weighted_average(scores: list[float]) -> float:
        """直近レース優先の加重平均を計算する。

        scores[0] が最新レース、scores[-1] が最古レース。
        weight[i] = WEIGHT_DECAY ^ i

        Args:
            scores: スピードスコアのリスト（最新順）

        Returns:
            加重平均スピード指数。空の場合は SPEED_INDEX_MEAN。
        """
        if not scores:
            return SPEED_INDEX_MEAN

        weights = [WEIGHT_DECAY**i for i in range(len(scores))]
        total_w = sum(weights)
        return round(sum(s * w for s, w in zip(scores, weights)) / total_w, 1)
