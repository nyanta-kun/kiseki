"""上昇相手指数算出Agent（Rivals Growth Index）

過去レースの同一出走馬の後続活躍度から、
対象馬が出走したレースの競走強度（相手レベル）を推定する指数。

算出ロジック:
  1. 対象馬の直近 N_LOOKBACK_RACES 走を参照（除外・取消を除く）
  2. 各過去レースの全出走馬（自分以外）を対象に後続活躍を評価:
     - 対象馬が負かした馬（opp_pos > my_pos）: フルボーナス（factor=1.0）
     - 対象馬に先着した馬（opp_pos <= my_pos）: FIELD_QUALITY_FACTOR=0.5倍ボーナス
       → 自分が6着でも上位馬が後続で活躍すれば「ハイレベルレース」の証明として評価
  3. 各相手馬について、その過去レース以降 SUBSEQUENT_WINDOW_DAYS 以内の
     最高グレード到達レースを取得（対象レース日より前のデータのみ使用）
  4. グレード上昇幅（uplift = 後の最高グレードランク − 元レースグレードランク）が
     正で、かつ 1〜3 着を達成していればスコアを加算:
       - 勝利（1着）: uplift × UPLIFT_UNIT × WIN_MULTIPLIER × field_factor
       - 2〜3着: uplift × UPLIFT_UNIT × PLACE_MULTIPLIER × field_factor
  5. 過去走の新しさに応じた減衰重み（RECENCY_DECAY^i）を乗算して累積
  6. score = 50 + min(MAX_BONUS, cumulative) に変換（中立=50、最大=100）

グレードランク（高いほど上位クラス、0=不明）:
  G1=9, G2=8, G3=7, OP/Listed/J.G=6,
  3勝クラス=5, 2勝クラス=4, 1勝クラス=3, 未勝利=2, 新馬=1
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Race, RaceEntry, RaceResult
from .base import IndexCalculator

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------
# パラメータ
# -------------------------------------------------------------------------
# 参照する過去走数
N_LOOKBACK_RACES = 5
# 相手馬の後続成績を参照する期間（日）
SUBSEQUENT_WINDOW_DAYS = 270
# グレード1段上昇あたりの基礎ボーナス
UPLIFT_UNIT = 10.0
# 勝利（1着）時の乗数
WIN_MULTIPLIER = 1.5
# 2〜3着時の乗数
PLACE_MULTIPLIER = 1.0
# 過去走の新しさ減衰係数（i番目の過去走: RECENCY_DECAY^i）
RECENCY_DECAY = 0.75
# スコアボーナス上限（50 + MAX_BONUS = 100 が最大）
MAX_BONUS = 50.0
# 中立スコア（データなし・上昇馬なし）
DEFAULT_SCORE = 50.0
# 「負かした」ではなく「同一レースで自分に先着した馬」が後続活躍した場合の係数
# これはレース全体のハイレベル証明として評価（フルボーナスの半額）
FIELD_QUALITY_FACTOR = 0.5


def _grade_rank(
    grade: str | None, prize_1st: int | None, race_type_code: str | None
) -> int:
    """レース情報からグレードランクを算出する。

    Args:
        grade: races.grade（G1/G2/G3/OP 等または None）
        prize_1st: races.prize_1st（百円単位）
        race_type_code: races.race_type_code

    Returns:
        グレードランク（1-9、高いほど上位クラス）。不明時は 0。
    """
    if grade:
        if "G1" in grade:
            return 9
        if "G2" in grade:
            return 8
        if "G3" in grade:
            return 7
        # OP / Listed / J.G 等（重賞以外の特別・リステッド）
        return 6

    if not prize_1st or not race_type_code:
        return 0

    p, tc = prize_1st, race_type_code

    if tc == "11":  # 2歳
        return 2 if p <= 58000 else 3
    if tc == "12":  # 3歳
        if p <= 62000:
            return 2
        if p <= 74000:
            return 3
        return 4
    if tc == "13":  # 3歳以上
        if p <= 100000:
            return 3
        if p <= 130000:
            return 4
        return 5
    if tc == "14":  # 4歳以上
        return 4 if p <= 100000 else 5

    return 0


class RivalsGrowthIndexCalculator(IndexCalculator):
    """上昇相手指数算出Agent。

    過去レースで負かした相手馬が後にグレードの高いレースで入着していれば、
    対象馬の実力上限が高いと判断してスコアを加算する。
    データがない場合や上昇馬がいない場合は中立値（50.0）を返す。
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
        """単一馬の上昇相手指数を算出する。

        Args:
            race_id: DB の races.id
            horse_id: DB の horses.id

        Returns:
            上昇相手指数（0-100、中立=50）。データ不足時は DEFAULT_SCORE。
        """
        race_result = await self.db.execute(select(Race).where(Race.id == race_id))
        race = race_result.scalar_one_or_none()
        if not race:
            return DEFAULT_SCORE
        batch = await self._compute_batch([horse_id], race.date)
        v = batch.get(horse_id)
        return v if v is not None else DEFAULT_SCORE

    async def calculate_batch(self, race_id: int) -> dict[int, float | None]:
        """レース全馬の上昇相手指数を一括算出する。

        N+1 を回避するため、全データを単一または少数のクエリで取得する。

        Args:
            race_id: DB の races.id

        Returns:
            {horse_id: rivals_growth_index} の dict。
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
        return await self._compute_batch(horse_ids, race.date)

    # ------------------------------------------------------------------
    # 内部メソッド
    # ------------------------------------------------------------------

    async def _compute_batch(
        self, horse_ids: list[int], race_date: str
    ) -> dict[int, float | None]:
        """複数馬の上昇相手指数を一括算出する。

        Args:
            horse_ids: 対象馬 ID リスト
            race_date: 対象レースの日付（YYYYMMDD）。この日付より前のデータのみ使用。

        Returns:
            {horse_id: rivals_growth_index} の dict。
        """
        if not horse_ids:
            return {}

        # ----------------------------------------------------------------
        # Step 1: 各馬の直近 N_LOOKBACK_RACES 走の成績を一括取得
        # ----------------------------------------------------------------
        stmt_past = (
            select(RaceResult, Race)
            .join(Race, RaceResult.race_id == Race.id)
            .where(
                RaceResult.horse_id.in_(horse_ids),
                Race.date < race_date,
                RaceResult.abnormality_code == 0,
                RaceResult.finish_position.is_not(None),
            )
            .order_by(RaceResult.horse_id, Race.date.desc())
        )
        past_rows = (await self.db.execute(stmt_past)).all()

        # horse_id → [(RaceResult, Race)] の直近N走マップ
        past_races_map: dict[int, list[tuple[RaceResult, Race]]] = defaultdict(list)
        count_map: dict[int, int] = defaultdict(int)
        for row in past_rows:
            hid = row.RaceResult.horse_id
            if count_map[hid] < N_LOOKBACK_RACES:
                past_races_map[hid].append((row.RaceResult, row.Race))
                count_map[hid] += 1

        if not past_races_map:
            return {hid: None for hid in horse_ids}

        # ----------------------------------------------------------------
        # Step 2: 過去レースの全出走馬成績を一括取得（相手馬特定用）
        # ----------------------------------------------------------------
        all_past_race_ids = list(
            {race.id for races_list in past_races_map.values() for _, race in races_list}
        )

        stmt_all = select(
            RaceResult.race_id, RaceResult.horse_id, RaceResult.finish_position
        ).where(
            RaceResult.race_id.in_(all_past_race_ids),
            RaceResult.abnormality_code == 0,
            RaceResult.finish_position.is_not(None),
        )
        all_result_rows = (await self.db.execute(stmt_all)).all()

        # race_id → [(horse_id, finish_position)] のマップ
        race_opponents_map: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for opp_row in all_result_rows:
            if opp_row.finish_position is not None:
                race_opponents_map[opp_row.race_id].append(
                    (opp_row.horse_id, int(opp_row.finish_position))
                )

        # ----------------------------------------------------------------
        # Step 3: 各馬の同一レース全出走馬を記録（着順関係を保持）
        # field_per_race[subject_horse_id][past_race_id][opp_horse_id] = opp_pos
        # 「負かした相手」(opp_pos > my_pos)はフルボーナス
        # 「先着した相手」(opp_pos <= my_pos)はFIELD_QUALITY_FACTOR倍のボーナス
        #   → 同一レースのハイレベル証明（6着でも上位馬が後続で勝てば評価UP）
        # ----------------------------------------------------------------
        field_per_race: dict[int, dict[int, dict[int, int]]] = defaultdict(
            lambda: defaultdict(dict)
        )

        for hid, past_list in past_races_map.items():
            for past_result, past_race in past_list:
                my_pos = past_result.finish_position
                if my_pos is None:
                    continue
                for opp_horse_id, opp_pos in race_opponents_map.get(past_race.id, []):
                    if opp_horse_id == hid:
                        continue
                    field_per_race[hid][past_race.id][opp_horse_id] = opp_pos

        all_field_ids = list(
            {
                opp_id
                for per_race in field_per_race.values()
                for opp_map in per_race.values()
                for opp_id in opp_map
            }
        )
        if not all_field_ids:
            return {hid: None for hid in horse_ids}

        # ----------------------------------------------------------------
        # Step 4: 相手馬の後続成績を一括取得
        # ----------------------------------------------------------------
        min_original_date = min(
            race.date
            for past_list in past_races_map.values()
            for _, race in past_list
        )

        stmt_subseq = (
            select(RaceResult, Race)
            .join(Race, RaceResult.race_id == Race.id)
            .where(
                RaceResult.horse_id.in_(all_field_ids),
                Race.date > min_original_date,
                Race.date < race_date,
                RaceResult.abnormality_code == 0,
                RaceResult.finish_position.is_not(None),
            )
            .order_by(RaceResult.horse_id, Race.date.asc())
        )
        subseq_rows = (await self.db.execute(stmt_subseq)).all()

        # beaten_horse_id → [(RaceResult, Race)] sorted by date asc
        subseq_map: dict[int, list[tuple[RaceResult, Race]]] = defaultdict(list)
        for subseq_row in subseq_rows:
            subseq_map[subseq_row.RaceResult.horse_id].append(
                (subseq_row.RaceResult, subseq_row.Race)
            )

        # ----------------------------------------------------------------
        # Step 5: スコア算出
        # ----------------------------------------------------------------
        result: dict[int, float | None] = {}
        for hid in horse_ids:
            horse_past_list: list[tuple[RaceResult, Race]] = past_races_map.get(hid, [])
            if not horse_past_list:
                result[hid] = None
                continue

            cumulative = 0.0

            for i, (_, past_race) in enumerate(horse_past_list):
                recency_weight = RECENCY_DECAY**i
                orig_rank = _grade_rank(
                    past_race.grade, past_race.prize_1st, past_race.race_type_code
                )
                if orig_rank == 0:
                    # グレード不明のレースは起点として使用しない
                    continue

                window_end = (
                    datetime.strptime(past_race.date, "%Y%m%d")
                    + timedelta(days=SUBSEQUENT_WINDOW_DAYS)
                ).strftime("%Y%m%d")

                my_pos = past_result.finish_position
                for opp_hid, opp_pos in field_per_race[hid].get(past_race.id, {}).items():
                    best_contribution = 0.0
                    # 着順関係による重み: 負かした馬=1.0、先着した馬=FIELD_QUALITY_FACTOR
                    if my_pos is not None and opp_pos > my_pos:
                        field_factor = 1.0
                    else:
                        field_factor = FIELD_QUALITY_FACTOR

                    for subseq_result, subseq_race in subseq_map.get(opp_hid, []):
                        if subseq_race.date <= past_race.date:
                            continue
                        if subseq_race.date > window_end:
                            break

                        subseq_rank = _grade_rank(
                            subseq_race.grade,
                            subseq_race.prize_1st,
                            subseq_race.race_type_code,
                        )
                        uplift = subseq_rank - orig_rank
                        if uplift <= 0:
                            continue

                        pos = subseq_result.finish_position
                        if pos == 1:
                            multiplier = WIN_MULTIPLIER
                        elif pos is not None and pos <= 3:
                            multiplier = PLACE_MULTIPLIER
                        else:
                            continue

                        contribution = uplift * UPLIFT_UNIT * multiplier * field_factor
                        if contribution > best_contribution:
                            best_contribution = contribution

                    cumulative += best_contribution * recency_weight

            score = DEFAULT_SCORE + min(MAX_BONUS, cumulative)
            result[hid] = round(score, 1)

        return result
