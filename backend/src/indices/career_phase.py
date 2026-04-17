"""成長曲線指数算出Agent（Career Phase Index）

直近N走の着順トレンドと馬齢フェーズから、
対象馬が上昇軌道にあるか下降軌道にあるかをスコア化する。

算出ロジック:
  1. 直近5走の正規化着順を算出
       winning_rank = 1.0 - (finish_position - 1) / max(head_count - 1, 1)
       1.0 = 1着, 0.0 = 最下位
  2. 異常区分(abnormality_code == 0)のみ対象
  3. x=[0,1,2,3,4]（新しいほど小さい）、y=winning_rank でOLS傾き推定
       x=0 が最新走。馬が上昇していれば最新の着順が良い（winning_rank が高い）
       → x=0 付近で y が高く x=4 付近で y が低い → slope は負
       improvement_score = -slope（正 = 上昇中）
  4. 馬齢補正（RaceEntry.horse_age で現在レース出走時の年齢を使用）
       2歳: +5（成長期）
       3歳 春（1-6月）: +5（クラシック成長期）
       3歳 秋以降・4歳以上: ±0
  5. slope_score = 50 + clip(improvement_score × 40, -30, +30)
  6. クラス慣れ補正（prize_1st でクラス判定）
       昇級2戦目（前走同クラス + 前々走は下クラス）: +CLASS_UP_2ND_BONUS
       昇級3戦目（前走2走同クラス + 3走前は下クラス）: +CLASS_UP_3RD_BONUS
       バックテスト（v17 2023-2026 16,198R）実証:
         昇級1戦目 ROI +6.8% / 昇級2戦目 ROI +15.1% / 昇級3戦目 ROI +12.2%
  7. final = clip(slope_score + age_adj + class_bonus, 0, 100)
  8. データ点 < 2 の場合は DEFAULT_SCORE(50.0) を返す
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Race, RaceEntry, RaceResult
from .base import IndexCalculator

logger = logging.getLogger(__name__)

# 参照する直近走数
LOOKBACK_RACES = 5
# データ点数が不足する場合のデフォルトスコア
DEFAULT_SCORE = 50.0
# 傾きをスコアへ変換するスケール係数
SLOPE_SCALE = 40.0
# slope_score の最大・最小クリップ幅
SLOPE_CLIP_MAX = 30.0
SLOPE_CLIP_MIN = -30.0
# 年齢ボーナス
AGE_BONUS_2YO = 5.0
AGE_BONUS_3YO_SPRING = 5.0
# 昇級慣れボーナス（バックテスト実証: v17 16,198R 2023-2026）
CLASS_UP_2ND_BONUS = 6.0  # 昇級2戦目: ROI差 +15.1%
CLASS_UP_3RD_BONUS = 4.0  # 昇級3戦目: ROI差 +12.2%

# prize_1st（百円単位）からクラス階層へのしきい値
# 3歳以上条件戦: 未勝利<70000<1勝<110000<2勝<145000<3勝<190000<OP
_CLASS_TIER_THRESHOLDS = [70_000, 110_000, 145_000, 190_000]


def _class_tier(prize_1st: int | None) -> int:
    """prize_1st からクラス階層を返す（低→高 = 1→5）。不明は 0。"""
    if prize_1st is None:
        return 0
    for tier, threshold in enumerate(_CLASS_TIER_THRESHOLDS, start=1):
        if prize_1st < threshold:
            return tier
    return len(_CLASS_TIER_THRESHOLDS) + 1  # オープン


def _compute_slope(x: list[float], y: list[float]) -> float:
    """最小二乗法で y = a*x + b の傾き a を返す。

    Args:
        x: 独立変数リスト（整数インデックス）
        y: 従属変数リスト（winning_rank）

    Returns:
        OLS 傾き a。計算不能時は 0.0。
    """
    n = len(x)
    if n < 2:
        return 0.0
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    ss_xx = sum((xi - mean_x) ** 2 for xi in x)
    if ss_xx < 1e-12:
        return 0.0
    ss_xy = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    return ss_xy / ss_xx


def _age_adjustment(horse_age: int | None, race_month: int) -> float:
    """馬齢と出走月から年齢補正値を返す。

    Args:
        horse_age: 馬齢（満年齢）。None の場合は 0。
        race_month: レース開催月（1-12）

    Returns:
        年齢補正ボーナス。
    """
    if horse_age is None:
        return 0.0
    if horse_age == 2:
        return AGE_BONUS_2YO
    if horse_age == 3 and race_month in (1, 2, 3, 4, 5, 6):
        return AGE_BONUS_3YO_SPRING
    return 0.0


class CareerPhaseIndexCalculator(IndexCalculator):
    """成長曲線指数算出Agent。

    直近N走の着順トレンドと馬齢フェーズを組み合わせ、
    上昇期・成熟期・下降期のフェーズをスコア化する。
    データが不足する場合は中立値（50.0）を返す。
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
        """単一馬の成長曲線指数を算出する。

        Args:
            race_id: DB の races.id（対象レース）
            horse_id: DB の horses.id

        Returns:
            成長曲線指数（0-100, 中立=50）。データ不足時は DEFAULT_SCORE。
        """
        race_result = await self.db.execute(select(Race).where(Race.id == race_id))
        race = race_result.scalar_one_or_none()
        if not race:
            return DEFAULT_SCORE

        # 馬齢を現在レースの RaceEntry から取得
        entry_result = await self.db.execute(
            select(RaceEntry).where(
                RaceEntry.race_id == race_id,
                RaceEntry.horse_id == horse_id,
            )
        )
        entry = entry_result.scalar_one_or_none()
        horse_age = entry.horse_age if entry else None

        past_data = await self._get_past_results(horse_id, race.date, race_id)
        race_month = datetime.strptime(race.date, "%Y%m%d").month
        return self._compute_score(past_data, horse_age, race_month, race.prize_1st)

    async def calculate_batch(self, race_id: int) -> dict[int, float]:
        """レース全馬の成長曲線指数を一括算出する。

        N+1 を回避するため、全馬のデータを単一または少数のクエリで取得する。

        Args:
            race_id: DB の races.id

        Returns:
            {horse_id: career_phase_index} の dict。
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
        # horse_id -> horse_age（現在レース出走時の馬齢）
        age_map: dict[int, int | None] = {e.horse_id: e.horse_age for e in entries}

        # 直近LOOKBACK_RACES走を一括取得（各馬）
        past_map = await self._get_past_results_batch(horse_ids, race.date, race_id)

        race_month = datetime.strptime(race.date, "%Y%m%d").month
        result: dict[int, float] = {}
        for entry in entries:
            hid = entry.horse_id
            past_data = past_map.get(hid, [])
            score = self._compute_score(past_data, age_map.get(hid), race_month, race.prize_1st)
            result[hid] = score

        return result

    # ------------------------------------------------------------------
    # 内部メソッド: データ取得
    # ------------------------------------------------------------------

    async def _get_past_results(
        self,
        horse_id: int,
        before_date: str,
        exclude_race_id: int,
    ) -> list[tuple[int, int, int | None]]:
        """単一馬の直近LOOKBACK_RACES走の (finish_position, head_count, prize_1st) を取得する。

        Args:
            horse_id: horses.id
            before_date: この日付より前のレースのみ（YYYYMMDD）
            exclude_race_id: 対象レースは除外

        Returns:
            [(finish_position, head_count, prize_1st), ...] 新しい順
        """
        stmt = (
            select(RaceResult.finish_position, Race.head_count, Race.prize_1st)
            .join(Race, RaceResult.race_id == Race.id)
            .where(
                RaceResult.horse_id == horse_id,
                Race.date < before_date,
                RaceResult.race_id != exclude_race_id,
                RaceResult.abnormality_code == 0,
                RaceResult.finish_position.is_not(None),
            )
            .order_by(Race.date.desc())
            .limit(LOOKBACK_RACES)
        )
        rows = (await self.db.execute(stmt)).all()
        return [
            (int(r.finish_position), int(r.head_count) if r.head_count else 1, r.prize_1st)
            for r in rows
        ]

    async def _get_past_results_batch(
        self,
        horse_ids: list[int],
        before_date: str,
        exclude_race_id: int,
    ) -> dict[int, list[tuple[int, int, int | None]]]:
        """複数馬の直近LOOKBACK_RACES走を一括取得する。

        Args:
            horse_ids: 対象 horses.id のリスト
            before_date: この日付より前のレース（YYYYMMDD）
            exclude_race_id: 対象レースは除外

        Returns:
            {horse_id: [(finish_position, head_count, prize_1st), ...] 新しい順}
        """
        if not horse_ids:
            return {}

        stmt = (
            select(RaceResult.horse_id, RaceResult.finish_position, Race.head_count, Race.prize_1st)
            .join(Race, RaceResult.race_id == Race.id)
            .where(
                RaceResult.horse_id.in_(horse_ids),
                Race.date < before_date,
                RaceResult.race_id != exclude_race_id,
                RaceResult.abnormality_code == 0,
                RaceResult.finish_position.is_not(None),
            )
            .order_by(RaceResult.horse_id, Race.date.desc())
        )
        rows = (await self.db.execute(stmt)).all()

        # horse_id ごとに最大 LOOKBACK_RACES 件収集
        result: dict[int, list[tuple[int, int, int | None]]] = defaultdict(list)
        count_map: dict[int, int] = defaultdict(int)
        for row in rows:
            hid = row.horse_id
            if count_map[hid] < LOOKBACK_RACES:
                fp = int(row.finish_position)
                hc = int(row.head_count) if row.head_count else 1
                result[hid].append((fp, hc, row.prize_1st))
                count_map[hid] += 1

        return dict(result)

    # ------------------------------------------------------------------
    # 内部メソッド: スコア算出
    # ------------------------------------------------------------------

    def _compute_score(
        self,
        past_data: list[tuple[int, int, int | None]],
        horse_age: int | None,
        race_month: int,
        current_prize_1st: int | None = None,
    ) -> float:
        """着順データと馬齢から成長曲線スコアを算出する。

        Args:
            past_data: [(finish_position, head_count, prize_1st), ...] 新しい順
            horse_age: 馬齢（満年齢）。None の場合は補正なし。
            race_month: レース開催月（1-12）
            current_prize_1st: 今走の1着賞金（百円単位）。クラス判定に使用。

        Returns:
            成長曲線指数（0-100, 中立=50）
        """
        if len(past_data) < 2:
            return DEFAULT_SCORE

        # 正規化着順を算出（1.0=1着、0.0=最下位）
        winning_ranks: list[float] = []
        for finish_position, head_count, _prize in past_data:
            denom = max(head_count - 1, 1)
            winning_rank = 1.0 - (finish_position - 1) / denom
            winning_ranks.append(winning_rank)

        # x=[0.0,1.0,2.0,...], y=winning_rank（x=0 が最新走）
        x = [float(i) for i in range(len(winning_ranks))]
        y = winning_ranks

        slope = _compute_slope(x, y)
        # x=0 が最新なので、改善傾向（最新が高い）では slope < 0
        improvement_score = -slope

        slope_score = 50.0 + max(SLOPE_CLIP_MIN, min(SLOPE_CLIP_MAX, improvement_score * SLOPE_SCALE))
        age_adj = _age_adjustment(horse_age, race_month)
        class_bonus = self._compute_class_bonus(past_data, current_prize_1st)
        final = max(0.0, min(100.0, slope_score + age_adj + class_bonus))
        return round(final, 1)

    def _compute_class_bonus(
        self,
        past_data: list[tuple[int, int, int | None]],
        current_prize_1st: int | None,
    ) -> float:
        """昇級2〜3戦目ボーナスを算出する。

        前走・前々走の prize_1st からクラス階層を判定し、
        今走が昇級後2戦目または3戦目に該当する場合にボーナスを返す。

        バックテスト（v17 16,198R 2023-2026）:
          昇級2戦目: ROI差 +15.1% → CLASS_UP_2ND_BONUS
          昇級3戦目: ROI差 +12.2% → CLASS_UP_3RD_BONUS

        Args:
            past_data: [(finish_position, head_count, prize_1st), ...] 新しい順
            current_prize_1st: 今走の1着賞金（百円単位）

        Returns:
            クラス慣れボーナス（0.0 / CLASS_UP_2ND_BONUS / CLASS_UP_3RD_BONUS）
        """
        curr_tier = _class_tier(current_prize_1st)
        if curr_tier == 0:
            return 0.0

        # 過去走のクラス階層リスト（新しい順）
        past_tiers = [_class_tier(d[2]) for d in past_data]

        # 昇級2戦目: 前走が同クラス、前々走が下クラス
        if len(past_tiers) >= 2:
            if past_tiers[0] == curr_tier and 0 < past_tiers[1] < curr_tier:
                return CLASS_UP_2ND_BONUS

        # 昇級3戦目: 前走・前々走が同クラス、3走前が下クラス
        if len(past_tiers) >= 3:
            if (
                past_tiers[0] == curr_tier
                and past_tiers[1] == curr_tier
                and 0 < past_tiers[2] < curr_tier
            ):
                return CLASS_UP_3RD_BONUS

        return 0.0
