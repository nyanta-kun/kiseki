"""ローテーション指数算出Agent

前走からの間隔（日数）と前走の着順・タイム偏差を組み合わせて
ローテーション適性をスコア化する。

算出ロジック:
  1. 馬の直近2戦（前走・前々走）の成績と日付を取得
  2. 間隔スコア（0-100）を日数帯で決定
     - 間隔なし（初出走）: 50.0（中立）
     - 7日以下（超過酷ローテ）: 20
     - 8-13日（中1週）: 40
     - 14-20日（中2週）: 60
     - 21-35日（中3-4週, 理想）: 80
     - 36-56日（2ヶ月以内）: 70
     - 57-83日（3ヶ月以内）: 55
     - 84-167日（半年以内）: 40
     - 168日以上（長期休養明け）: 30
  3. 前走着順ボーナス（0-20）:
     1着: +20, 2着: +15, 3着: +10, 4-5着: +5, それ以外: 0
  4. 前走タイム偏差ボーナス（0-10）:
     前走スピードスコアが50超: +(前走スピード-50)/10 * 10（最大10）
  5. 合計スコア = interval_score + position_bonus + time_bonus（上限100）
  6. clip(0, 100) して返す
  7. 前走データなし（初出走）: 50.0

制約:
  - 除外・取消（abnormality_code > 0）のレースは前走データとして使用しない
  - Race.date は "YYYYMMDD" 形式文字列
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_
from sqlalchemy.orm import Session

from ..db.models import Race, RaceEntry, RaceResult
from ..utils.constants import SPEED_INDEX_MEAN
from .base import IndexCalculator

logger = logging.getLogger(__name__)

# 過去何戦を参照するか（前走・前々走の2戦分 + 余裕）
LOOKBACK_RACES = 2
# 指数クリップ範囲
INDEX_MIN = 0.0
INDEX_MAX = 100.0
# 初出走時のデフォルトスコア（中立）
DEFAULT_SCORE = 50.0


def _interval_score(days: int) -> float:
    """前走からの間隔日数をスコアに変換する。

    Args:
        days: 前走からの日数（0 以上の整数）

    Returns:
        間隔スコア（0-100）
    """
    if days <= 7:
        return 20.0
    elif days <= 13:
        return 40.0
    elif days <= 20:
        return 60.0
    elif days <= 35:
        return 80.0
    elif days <= 56:
        return 70.0
    elif days <= 83:
        return 55.0
    elif days <= 167:
        return 40.0
    else:
        return 30.0


def _position_bonus(finish_position: int | None) -> float:
    """前走着順をボーナス点に変換する。

    Args:
        finish_position: 着順（None の場合は 0）

    Returns:
        着順ボーナス（0-20）
    """
    if finish_position is None:
        return 0.0
    if finish_position == 1:
        return 20.0
    elif finish_position == 2:
        return 15.0
    elif finish_position == 3:
        return 10.0
    elif finish_position <= 5:
        return 5.0
    else:
        return 0.0


def _time_bonus(speed_score: float | None) -> float:
    """前走スピードスコアをタイム偏差ボーナス点に変換する。

    Args:
        speed_score: 前走スピードスコア（0-100、None の場合は 0）

    Returns:
        タイム偏差ボーナス（0-10）
    """
    if speed_score is None or speed_score <= SPEED_INDEX_MEAN:
        return 0.0
    raw = (speed_score - SPEED_INDEX_MEAN) / 10.0 * 10.0
    return min(10.0, raw)


def _parse_date(date_str: str) -> datetime:
    """YYYYMMDD 形式の文字列を datetime に変換する。

    Args:
        date_str: "YYYYMMDD" 形式の文字列

    Returns:
        datetime オブジェクト
    """
    return datetime.strptime(date_str, "%Y%m%d")


class RotationIndexCalculator(IndexCalculator):
    """ローテーション指数算出Agent。

    IndexCalculator を継承し、単一馬（calculate）と
    レース全馬バッチ（calculate_batch）の両インターフェースを提供する。
    """

    def __init__(self, db: Session) -> None:
        """初期化。

        Args:
            db: SQLAlchemy セッション
        """
        super().__init__(db)

    # ------------------------------------------------------------------
    # 公開インターフェース
    # ------------------------------------------------------------------

    def calculate(self, race_id: int, horse_id: int) -> float:
        """単一馬のローテーション指数を算出する。

        Args:
            race_id: DB の races.id（対象レース）
            horse_id: DB の horses.id

        Returns:
            ローテーション指数（0-100, 中立50）。データ不足時は DEFAULT_SCORE。
        """
        race = self.db.query(Race).filter(Race.id == race_id).first()
        if not race:
            logger.warning(f"Race not found: race_id={race_id}")
            return DEFAULT_SCORE

        rows = self._get_past_results_for_horse(horse_id, race.date, race_id)
        return self._compute_rotation_index(rows, race.date)

    def calculate_batch(self, race_id: int) -> dict[int, float]:
        """レース全馬のローテーション指数を一括算出する。

        N+1 を回避するため、全馬の過去レース結果を単一クエリで取得する。

        Args:
            race_id: DB の races.id

        Returns:
            {horse_id: rotation_index} のdict。エントリが存在しない場合は空dict。
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
            result[entry.horse_id] = self._compute_rotation_index(rows, race.date)

        return result

    # ------------------------------------------------------------------
    # 内部メソッド
    # ------------------------------------------------------------------

    def _get_past_results_for_horse(
        self, horse_id: int, before_date: str, exclude_race_id: int
    ) -> list[Any]:
        """単一馬の過去レース結果（最大 LOOKBACK_RACES 件）を取得する。

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

    def _compute_rotation_index(self, rows: list[Any], target_date: str) -> float:
        """過去レース結果リストからローテーション指数を算出する。

        Args:
            rows: [(RaceResult, Race), ...] 前走・前々走の結果（日付降順）
            target_date: 対象レースの日付（YYYYMMDD）

        Returns:
            ローテーション指数（0-100）。前走データなし（初出走）は DEFAULT_SCORE。
        """
        if not rows:
            return DEFAULT_SCORE

        # 直近1戦（前走）のデータ
        prev_row = rows[0]
        prev_result: RaceResult = prev_row.RaceResult
        prev_race: Race = prev_row.Race

        # 間隔スコア
        try:
            target_dt = _parse_date(target_date)
            prev_dt = _parse_date(prev_race.date)
            days = (target_dt - prev_dt).days
        except (ValueError, AttributeError) as e:
            logger.warning(f"日付解析エラー: target={target_date}, prev={prev_race.date}: {e}")
            return DEFAULT_SCORE

        interval = _interval_score(days)

        # 前走着順ボーナス
        pos_bonus = _position_bonus(prev_result.finish_position)

        # 前走タイム偏差ボーナス
        speed_score = self._estimate_speed_score(prev_result, prev_race)
        t_bonus = _time_bonus(speed_score)

        total = interval + pos_bonus + t_bonus
        return round(max(INDEX_MIN, min(INDEX_MAX, total)), 1)

    def _estimate_speed_score(self, result: RaceResult, race: Race) -> float | None:
        """前走のタイムから簡易スピードスコアを推定する。

        同コース・距離・馬場の全着順平均タイムとの差を正規化して返す。
        基準タイムのサンプル不足時は None を返す。

        Args:
            result: 前走のレース結果
            race: 前走のレース情報

        Returns:
            スピードスコア（0-100）、または None（算出不可の場合）
        """
        if result.finish_time is None:
            return None

        # 同コース・距離・馬場の平均・標準偏差を取得
        from sqlalchemy import func

        row = (
            self.db.query(
                func.avg(RaceResult.finish_time).label("avg_time"),
                func.stddev_pop(RaceResult.finish_time).label("std_time"),
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

        if row is None or row.cnt is None or int(row.cnt) < 5:
            return None

        avg = float(row.avg_time) if row.avg_time else 0.0
        std = float(row.std_time) if row.std_time else 0.0

        if std < 0.01:
            return None

        actual_time = float(result.finish_time)
        diff = avg - actual_time
        score = (diff / std) * 10.0 + SPEED_INDEX_MEAN
        return max(INDEX_MIN, min(INDEX_MAX, score))
