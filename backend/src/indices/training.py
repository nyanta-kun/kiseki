"""調教指数算出Agent（レース成績トレンドによる近似実装）

TM（調教）レコードが未実装のため、直近レース成績のパフォーマンス
トレンドで「仕上がり状態」を近似する。

算出ロジック（3軸合成）:
  1. タイム偏差トレンド (weight=0.40):
     直近5走の「同コース・距離の基準タイム比偏差スコア（0-100）」に
     線形回帰を当て、傾き（改善方向）をスコア化する。
     - 最大改善傾向（10点/走）→ 上位スコア
     - 悪化傾向 → 下位スコア
     - データ不足（2走未満）→ NEUTRAL(50)

  2. 上がり3F改善 (weight=0.30):
     直近3走の後半3Fタイムの変化（値が小さいほど速い）。
     直近が最速なら高スコア、悪化なら低スコア。
     - 変化なし → NEUTRAL
     - 0.5秒/走 改善ペース → 上位スコア

  3. 体重コンディション (weight=0.30):
     直前レースからの体重変化（weight_change）で馬の状態を評価。
     - ±2kg以内（安定） → 55
     - +3 〜 +6kg（ほどよい増加） → 52
     - -3 〜 -6kg（絞れた） → 52
     - 7kg以上の増減（大幅変化） → 42
     - データなし → NEUTRAL(50)

合計は各軸スコアの重み付き平均で [0, 100] にクリップ。

制約:
  - 除外・取消（abnormality_code > 0）は参照しない
  - finish_time が NULL のレースは時系列から除外
  - 同コース・距離の比較基準が5件未満の場合はタイム軸をスキップ
"""

from __future__ import annotations

import logging
from collections import defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db.models import Race, RaceEntry, RaceResult
from ..utils.constants import SPEED_INDEX_MEAN
from .base import IndexCalculator

logger = logging.getLogger(__name__)

# 参照戦数
LOOKBACK_RACES = 5
LAST3F_LOOKBACK = 3
# 指数クリップ範囲
INDEX_MIN = 0.0
INDEX_MAX = 100.0
NEUTRAL = SPEED_INDEX_MEAN  # 50.0

# 各軸の重み
W_TIME_TREND = 0.40
W_LAST3F = 0.30
W_WEIGHT = 0.30

# 基準タイム集計の最低サンプル数
MIN_BASELINE_SAMPLE = 5


# ---------------------------------------------------------------------------
# 内部ユーティリティ
# ---------------------------------------------------------------------------

def _weight_cond_score(weight_change: int | None) -> float:
    """体重変化から状態スコアを算出する。

    Args:
        weight_change: 前走比体重増減（kg単位、None=不明）

    Returns:
        状態スコア（0-100）
    """
    if weight_change is None:
        return NEUTRAL
    abs_change = abs(weight_change)
    if abs_change <= 2:
        return 55.0   # 安定
    elif abs_change <= 6:
        return 52.0   # 許容範囲
    elif abs_change <= 10:
        return 45.0   # やや大きな変化
    else:
        return 38.0   # 大幅変化（懸念）


def _linear_trend(values: list[float]) -> float:
    """数値リスト（古い順）から線形回帰の傾き（未正規化）を返す。

    Args:
        values: 時系列スコアリスト（古い→新しい順）

    Returns:
        傾き（正=改善、負=悪化）
    """
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    x_mean = (n - 1) / 2
    y_mean = sum(values) / n
    num = sum((xs[i] - x_mean) * (values[i] - y_mean) for i in range(n))
    den = sum((xs[i] - x_mean) ** 2 for i in range(n))
    return num / den if den > 0 else 0.0


def _trend_to_score(slope: float, scale: float = 2.0) -> float:
    """傾きをスコア（0-100）に変換する。

    slope=0 → 50、slope=+scale → 80、slope=-scale → 20

    Args:
        slope: 線形回帰の傾き
        scale: ±scale で 80/20 に対応するスケール

    Returns:
        スコア（0-100）
    """
    score = NEUTRAL + (slope / scale) * 30.0
    return max(INDEX_MIN, min(INDEX_MAX, score))


# ---------------------------------------------------------------------------
# メインクラス
# ---------------------------------------------------------------------------

class TrainingIndexCalculator(IndexCalculator):
    """調教指数算出Agent（レース成績トレンドによる近似）。

    IndexCalculator を継承し calculate / calculate_batch を提供する。
    """

    def __init__(self, db: Session) -> None:
        super().__init__(db)
        # コース・距離別 基準タイム統計キャッシュ {(course, distance, surface): (avg, std, cnt)}
        self._baseline_cache: dict[tuple, tuple[float, float, int]] = {}

    # ------------------------------------------------------------------
    # 公開インターフェース
    # ------------------------------------------------------------------

    def calculate(self, race_id: int, horse_id: int) -> float:
        race = self.db.query(Race).filter(Race.id == race_id).first()
        if not race:
            return NEUTRAL
        rows = self._fetch_past_results([horse_id], race.date, race_id)
        return self._compute(rows.get(horse_id, []), race)

    def calculate_batch(self, race_id: int) -> dict[int, float]:
        race = self.db.query(Race).filter(Race.id == race_id).first()
        if not race:
            return {}
        entries = self.db.query(RaceEntry).filter(RaceEntry.race_id == race_id).all()
        if not entries:
            return {}

        horse_ids = [e.horse_id for e in entries]
        rows_map = self._fetch_past_results(horse_ids, race.date, race_id)

        return {
            entry.horse_id: self._compute(rows_map.get(entry.horse_id, []), race)
            for entry in entries
        }

    # ------------------------------------------------------------------
    # 内部メソッド
    # ------------------------------------------------------------------

    def _fetch_past_results(
        self, horse_ids: list[int], before_date: str, exclude_race_id: int
    ) -> dict[int, list[Any]]:
        """複数馬の直近レース結果を一括取得する（最大 LOOKBACK_RACES 件）。"""
        if not horse_ids:
            return {}

        rows = (
            self.db.query(RaceResult, Race)
            .join(Race, RaceResult.race_id == Race.id)
            .filter(
                RaceResult.horse_id.in_(horse_ids),
                Race.date < before_date,
                RaceResult.race_id != exclude_race_id,
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

    def _compute(self, rows: list[Any], race: Race) -> float:
        """3軸スコアを合成して調教指数を返す。

        Args:
            rows: [(RaceResult, Race), ...] 直近最大5走（新しい順）
            race: 対象レース（基準タイム取得に使用）

        Returns:
            調教指数（0-100）
        """
        if not rows:
            return NEUTRAL

        time_score = self._time_trend_score(rows, race)
        last3f_score = self._last3f_trend_score(rows[:LAST3F_LOOKBACK])
        weight_score = _weight_cond_score(
            int(rows[0].RaceResult.weight_change)
            if rows[0].RaceResult.weight_change is not None
            else None
        )

        composite = (
            time_score * W_TIME_TREND
            + last3f_score * W_LAST3F
            + weight_score * W_WEIGHT
        )
        return round(max(INDEX_MIN, min(INDEX_MAX, composite)), 1)

    def _time_trend_score(self, rows: list[Any], ref_race: Race) -> float:
        """直近レースのタイム偏差スコアのトレンドから調教スコアを算出する。

        Args:
            rows: 直近レース結果（新しい順）
            ref_race: 対象レース（コース・距離・馬場参照）

        Returns:
            タイムトレンドスコア（0-100）
        """
        # タイムがある行を古い順に並べてスコア化
        scored: list[float] = []
        for row in reversed(rows):  # 古い→新しい
            rr: RaceResult = row.RaceResult
            r: Race = row.Race
            if rr.finish_time is None:
                continue
            dev = self._time_deviation(float(rr.finish_time), r)
            if dev is not None:
                scored.append(dev)

        if len(scored) < 2:
            return NEUTRAL

        slope = _linear_trend(scored)
        # 1走あたり2点改善が「良好トレンド」の目安
        return _trend_to_score(slope, scale=2.0)

    def _time_deviation(self, finish_time: float, race: Race) -> float | None:
        """指定レースのタイムを同コース・距離・馬場の基準と比較してスコア化する。

        Args:
            finish_time: 走破タイム（秒）
            race: レース情報

        Returns:
            偏差スコア（0-100）、基準不足時は None
        """
        key = (race.course, race.distance, race.surface)
        if key not in self._baseline_cache:
            row = (
                self.db.query(
                    func.avg(RaceResult.finish_time).label("avg"),
                    func.stddev_pop(RaceResult.finish_time).label("std"),
                    func.count(RaceResult.id).label("cnt"),
                )
                .join(Race, RaceResult.race_id == Race.id)
                .filter(
                    Race.course == race.course,
                    Race.distance == race.distance,
                    Race.surface == race.surface,
                    RaceResult.finish_time.isnot(None),
                    RaceResult.abnormality_code == 0,
                )
                .first()
            )
            if row and row.cnt and int(row.cnt) >= MIN_BASELINE_SAMPLE:
                self._baseline_cache[key] = (
                    float(row.avg), float(row.std or 1.0), int(row.cnt)
                )
            else:
                self._baseline_cache[key] = (0.0, 0.0, 0)

        avg, std, cnt = self._baseline_cache[key]
        if cnt < MIN_BASELINE_SAMPLE or std < 0.01:
            return None

        # 速いほど高スコア（基準より速い → 正のdiff）
        diff = avg - finish_time
        score = (diff / std) * 10.0 + NEUTRAL
        return max(INDEX_MIN, min(INDEX_MAX, score))

    def _last3f_trend_score(self, rows: list[Any]) -> float:
        """直近3走の上がり3F改善トレンドからスコアを算出する。

        Args:
            rows: 直近最大3走（新しい順）

        Returns:
            上がり3Fトレンドスコア（0-100）
        """
        values: list[float] = []
        for row in reversed(rows):  # 古い→新しい
            last3f = row.RaceResult.last_3f
            if last3f is not None:
                # last_3f は秒（小さいほど速い）→ 高スコアに反転
                values.append(-float(last3f))

        if len(values) < 2:
            return NEUTRAL

        # slope が正 = last_3fの負値が増加 = 実タイムが減少 = 速くなっている
        slope = _linear_trend(values)
        # 0.1秒/走 改善（slope=+0.1）を良好と定義
        return _trend_to_score(slope, scale=0.1)
