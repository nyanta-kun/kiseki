"""上がり3ハロン指数算出Agent

馬の後3ハロンタイム（last_3f）を同レース内のフィールド分布と比較し、
末脚・加速力の相対的な強さをスコア化する。

算出ロジック:
  1. 対象馬の過去 LOOKBACK_RACES 戦の成績を取得
  2. 各過去レースで last_3f が有効な場合、そのレースの全出走馬の last_3f 分布を取得
  3. z = (フィールド平均 - 馬のlast_3f) / フィールド標準偏差
     → タイムは低いほど優秀なので (平均 - 馬) で正規化
  4. スコア = z × 10 + 50（平均50, σ=10）にクリップ
  5. 直近レースほど重みを大きくして加重平均（減衰率 WEIGHT_DECAY）

制約:
  - last_3f が None または abnormality_code > 0 の成績は除外
  - フィールドサンプル数が MIN_FIELD_SAMPLE 未満のレースはスキップ
  - 過去レースが MIN_RACES 戦未満の場合は SPEED_INDEX_MEAN=50.0 を返す
  - バッチ処理はフィールド統計を一括取得してN+1を回避
"""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from typing import Any

from sqlalchemy import and_
from sqlalchemy.orm import Session

from ..db.models import Race, RaceEntry, RaceResult
from ..utils.constants import SPEED_INDEX_MEAN, SPEED_INDEX_STD
from .base import IndexCalculator

logger = logging.getLogger(__name__)

# 過去何戦を参照するか
LOOKBACK_RACES = 10
# スコア算出に必要なフィールドの最低サンプル数
MIN_FIELD_SAMPLE = 4
# 最低有効戦数（これ未満は SPEED_INDEX_MEAN を返す）
MIN_RACES = 2
# 加重平均の減衰率（直近から遡るほど 0.8^n 倍）
WEIGHT_DECAY = 0.8
# 指数クリップ範囲
INDEX_MIN = 0.0
INDEX_MAX = 100.0


class Last3FIndexCalculator(IndexCalculator):
    """上がり3ハロン指数算出Agent。

    IndexCalculator を継承し、単一馬（calculate）と
    レース全馬バッチ（calculate_batch）の両インターフェースを提供する。
    """

    def __init__(self, db: Session) -> None:
        """初期化。

        Args:
            db: SQLAlchemy セッション
        """
        super().__init__(db)
        # フィールド統計キャッシュ（race_id → (mean, std) | None）
        self._field_stats_cache: dict[int, tuple[float, float] | None] = {}

    # ------------------------------------------------------------------
    # 公開インターフェース
    # ------------------------------------------------------------------

    def calculate(self, race_id: int, horse_id: int) -> float:
        """単一馬の上がり3ハロン指数を算出する。

        Args:
            race_id: DB の races.id
            horse_id: DB の horses.id

        Returns:
            上がり3ハロン指数（0-100, 平均50）。データ不足時は SPEED_INDEX_MEAN。
        """
        race = self.db.query(Race).filter(Race.id == race_id).first()
        if not race:
            logger.warning(f"Race not found: race_id={race_id}")
            return SPEED_INDEX_MEAN

        past_rows = self._get_past_results(horse_id, race.date, race_id)
        race_ids = {r.race_id for r, _, _ in past_rows if r.last_3f is not None}
        field_stats = {rid: self._get_field_stats(rid) for rid in race_ids}
        scores = self._compute_scores(past_rows, field_stats)
        return self._weighted_average(scores)

    def calculate_batch(self, race_id: int) -> dict[int, float]:
        """レース全馬の上がり3ハロン指数を一括算出する。

        Args:
            race_id: DB の races.id

        Returns:
            {horse_id: last3f_index} のdict。
        """
        race = self.db.query(Race).filter(Race.id == race_id).first()
        if not race:
            logger.warning(f"Race not found: race_id={race_id}")
            return {}

        entries = self.db.query(RaceEntry).filter(RaceEntry.race_id == race_id).all()
        if not entries:
            return {}

        horse_ids = [e.horse_id for e in entries]

        # 全馬の過去結果を一括取得
        rows_map = self._get_past_results_batch(horse_ids, race.date, race_id)

        # フィールド統計を一括取得（N+1回避）
        past_race_ids: set[int] = set()
        for rows in rows_map.values():
            for result, _, _ in rows:
                if result.last_3f is not None:
                    past_race_ids.add(result.race_id)

        field_stats = self._get_field_stats_batch(past_race_ids)

        result: dict[int, float] = {}
        for entry in entries:
            rows = rows_map.get(entry.horse_id, [])
            scores = self._compute_scores(rows, field_stats)
            result[entry.horse_id] = self._weighted_average(scores)

        return result

    # ------------------------------------------------------------------
    # 内部メソッド
    # ------------------------------------------------------------------

    def _get_past_results(
        self, horse_id: int, before_date: str, exclude_race_id: int
    ) -> list[Any]:
        """単一馬の過去レース結果を取得する（last_3f あり優先）。"""
        return (
            self.db.query(RaceResult, Race, RaceEntry)
            .join(Race, RaceResult.race_id == Race.id)
            .join(
                RaceEntry,
                and_(
                    RaceEntry.race_id == RaceResult.race_id,
                    RaceEntry.horse_id == RaceResult.horse_id,
                ),
            )
            .filter(
                RaceResult.horse_id == horse_id,
                Race.date < before_date,
                RaceResult.race_id != exclude_race_id,
                RaceResult.abnormality_code == 0,
            )
            .order_by(Race.date.desc())
            .limit(LOOKBACK_RACES)
            .all()
        )

    def _get_past_results_batch(
        self, horse_ids: list[int], before_date: str, exclude_race_id: int
    ) -> dict[int, list[Any]]:
        """複数馬の過去レース結果を単一クエリで一括取得する。"""
        if not horse_ids:
            return {}

        rows = (
            self.db.query(RaceResult, Race, RaceEntry)
            .join(Race, RaceResult.race_id == Race.id)
            .join(
                RaceEntry,
                and_(
                    RaceEntry.race_id == RaceResult.race_id,
                    RaceEntry.horse_id == RaceResult.horse_id,
                ),
            )
            .filter(
                RaceResult.horse_id.in_(horse_ids),
                Race.date < before_date,
                RaceResult.race_id != exclude_race_id,
                RaceResult.abnormality_code == 0,
            )
            .order_by(RaceResult.horse_id, Race.date.desc())
            .all()
        )

        rows_map: dict[int, list[Any]] = defaultdict(list)
        counts: dict[int, int] = defaultdict(int)
        for row in rows:
            result, race, entry = row
            hid = result.horse_id
            if counts[hid] < LOOKBACK_RACES:
                rows_map[hid].append(row)
                counts[hid] += 1

        return rows_map

    def _get_field_stats(self, race_id: int) -> tuple[float, float] | None:
        """単一レースの last_3f フィールド統計 (mean, std) を返す。

        キャッシュを利用して重複クエリを回避する。
        MIN_FIELD_SAMPLE 未満のレースは None を返す。
        """
        if race_id in self._field_stats_cache:
            return self._field_stats_cache[race_id]

        values = (
            self.db.query(RaceResult.last_3f)
            .filter(
                RaceResult.race_id == race_id,
                RaceResult.last_3f.isnot(None),
                RaceResult.abnormality_code == 0,
            )
            .all()
        )
        vals = [float(v[0]) for v in values]

        if len(vals) < MIN_FIELD_SAMPLE:
            self._field_stats_cache[race_id] = None
            return None

        mean = statistics.mean(vals)
        std = statistics.stdev(vals) if len(vals) >= 2 else 0.0
        if std < 0.1:
            self._field_stats_cache[race_id] = None
            return None

        result = (mean, std)
        self._field_stats_cache[race_id] = result
        return result

    def _get_field_stats_batch(
        self, race_ids: set[int]
    ) -> dict[int, tuple[float, float] | None]:
        """複数レースのフィールド統計を一括取得する。"""
        if not race_ids:
            return {}

        # キャッシュにあるものはスキップ
        uncached = {rid for rid in race_ids if rid not in self._field_stats_cache}

        if uncached:
            rows = (
                self.db.query(RaceResult.race_id, RaceResult.last_3f)
                .filter(
                    RaceResult.race_id.in_(uncached),
                    RaceResult.last_3f.isnot(None),
                    RaceResult.abnormality_code == 0,
                )
                .all()
            )

            # race_id ごとにまとめる
            vals_map: dict[int, list[float]] = defaultdict(list)
            for race_id, last3f in rows:
                vals_map[race_id].append(float(last3f))

            for rid in uncached:
                vals = vals_map.get(rid, [])
                if len(vals) < MIN_FIELD_SAMPLE:
                    self._field_stats_cache[rid] = None
                    continue
                mean = statistics.mean(vals)
                std = statistics.stdev(vals) if len(vals) >= 2 else 0.0
                if std < 0.1:
                    self._field_stats_cache[rid] = None
                    continue
                self._field_stats_cache[rid] = (mean, std)

        return {rid: self._field_stats_cache.get(rid) for rid in race_ids}

    def _compute_scores(
        self,
        past_rows: list[Any],
        field_stats: dict[int, tuple[float, float] | None],
    ) -> list[float]:
        """各過去レースのスコアリストを計算する（直近順）。

        Args:
            past_rows: [(RaceResult, Race, RaceEntry), ...] 日付降順
            field_stats: {race_id: (mean, std) | None}

        Returns:
            スコアリスト（直近 → 古い順, 0-100）
        """
        scores: list[float] = []
        for result, race, entry in past_rows:
            if result.last_3f is None:
                continue
            stats = field_stats.get(result.race_id)
            if stats is None:
                continue

            mean, std = stats
            horse_val = float(result.last_3f)
            # タイムが低い（速い）ほど高スコア → (mean - horse) / std
            z = (mean - horse_val) / std
            score = z * SPEED_INDEX_STD + SPEED_INDEX_MEAN
            score = round(max(INDEX_MIN, min(INDEX_MAX, score)), 1)
            scores.append(score)

        return scores

    def _weighted_average(self, scores: list[float]) -> float:
        """直近レースほど高重みの加重平均を計算する。

        Args:
            scores: 直近 → 古い順のスコアリスト

        Returns:
            加重平均スコア。スコアが MIN_RACES 未満なら SPEED_INDEX_MEAN。
        """
        if len(scores) < MIN_RACES:
            return SPEED_INDEX_MEAN

        weights = [WEIGHT_DECAY ** i for i in range(len(scores))]
        total_weight = sum(weights)
        weighted_sum = sum(s * w for s, w in zip(scores, weights))
        return round(weighted_sum / total_weight, 1)
