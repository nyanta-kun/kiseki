"""騎手×厩舎コンビ指数算出Agent（Jockey-Trainer Combo Index）

特定の騎手と厩舎の組み合わせが単独騎手平均を上回る勝率を出しているかを
スコア化する。

算出ロジック:
  1. 現在レースの (horse_id, jockey_id, trainer_id) を取得
  2. 各 (jockey_id, trainer_id) コンビの過去成績を集計:
       - コンビ勝率 = コンビ勝利数 / コンビ出走数
  3. 騎手単独の過去成績を集計:
       - 単独騎手勝率 = 騎手全勝利数 / 騎手全出走数
  4. lift = combo_win_rate - solo_jockey_win_rate
  5. score = 50 + clip(lift × 200, -15, +20)
  6. コンビ出走数 < 5 の場合は DEFAULT_SCORE(50.0)
  7. データなし時も DEFAULT_SCORE(50.0)

バッチ実装:
  1. 現在レース全馬の (horse_id, jockey_id, trainer_id) を取得
  2. ユニークな (jockey_id, trainer_id) コンビを収集
  3. 各コンビの過去統計をクエリ
  4. ユニークな jockey_id の単独統計をクエリ
  5. スコア算出
"""

from __future__ import annotations

import logging

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Race, RaceEntry, RaceResult
from .base import IndexCalculator

logger = logging.getLogger(__name__)

# コンビ出走数の最小要件
MIN_COMBO_RACES = 5
# lift のスケール係数（lift=0.1 → +20点）
LIFT_SCALE = 200.0
# スコアクリップ範囲
CLIP_MIN = -15.0
CLIP_MAX = 20.0
# デフォルトスコア（中立）
DEFAULT_SCORE = 50.0


class JockeyTrainerComboIndexCalculator(IndexCalculator):
    """騎手×厩舎コンビ指数算出Agent。

    騎手と調教師の組み合わせが統計的に単独騎手平均より高い勝率を
    示しているかをスコア化する。コンビ出走数が不足する場合や
    データがない場合は中立値（50.0）を返す。
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
        """単一馬の騎手×厩舎コンビ指数を算出する。

        Args:
            race_id: DB の races.id（対象レース）
            horse_id: DB の horses.id

        Returns:
            騎手×厩舎コンビ指数（0-100, 中立=50）。データ不足時は DEFAULT_SCORE。
        """
        race_result = await self.db.execute(select(Race).where(Race.id == race_id))
        race = race_result.scalar_one_or_none()
        if not race:
            return DEFAULT_SCORE

        batch = await self._compute_batch([horse_id], race)
        v = batch.get(horse_id)
        return v if v is not None else DEFAULT_SCORE

    async def calculate_batch(self, race_id: int) -> dict[int, float | None]:
        """レース全馬の騎手×厩舎コンビ指数を一括算出する。

        N+1 を回避するため、全馬のデータを少数のクエリで取得する。

        Args:
            race_id: DB の races.id

        Returns:
            {horse_id: jockey_trainer_combo_index} の dict。
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
    ) -> dict[int, float | None]:
        """複数馬の騎手×厩舎コンビ指数を一括算出する。

        Args:
            horse_ids: 対象馬 ID リスト
            current_race: 現在の Race オブジェクト

        Returns:
            {horse_id: jockey_trainer_combo_index} の dict。
        """
        if not horse_ids:
            return {}

        before_date = current_race.date

        # ----------------------------------------------------------------
        # Step 1: 現在レースの全馬エントリ (horse_id, jockey_id, trainer_id) を取得
        # ----------------------------------------------------------------
        entry_result = await self.db.execute(
            select(
                RaceEntry.horse_id,
                RaceEntry.jockey_id,
                RaceEntry.trainer_id,
            ).where(
                RaceEntry.race_id == current_race.id,
                RaceEntry.horse_id.in_(horse_ids),
            )
        )
        entry_rows = entry_result.all()

        # horse_id -> (jockey_id, trainer_id)
        horse_combo: dict[int, tuple[int | None, int | None]] = {}
        for row in entry_rows:
            horse_combo[row.horse_id] = (row.jockey_id, row.trainer_id)

        # ----------------------------------------------------------------
        # Step 2: ユニークなコンビと騎手IDを収集
        # ----------------------------------------------------------------
        unique_combos: set[tuple[int, int]] = set()
        unique_jockey_ids: set[int] = set()
        for hid, (jid, tid) in horse_combo.items():
            if jid is not None and tid is not None:
                unique_combos.add((jid, tid))
                unique_jockey_ids.add(jid)

        if not unique_combos:
            return {hid: None for hid in horse_ids}

        # ----------------------------------------------------------------
        # Step 3: 各コンビの過去統計を取得
        # 各 (jockey_id, trainer_id) コンビを順次クエリ（最大 18 コンビ）
        # ----------------------------------------------------------------
        combo_stats: dict[tuple[int, int], tuple[int, int]] = {}  # (wins, total)
        for jid, tid in unique_combos:
            stmt = (
                select(
                    func.count().label("total"),
                    func.sum(
                        case((RaceResult.finish_position == 1, 1), else_=0)
                    ).label("wins"),
                )
                .join(Race, RaceResult.race_id == Race.id)
                .join(
                    RaceEntry,
                    (RaceEntry.race_id == RaceResult.race_id)
                    & (RaceEntry.horse_id == RaceResult.horse_id),
                )
                .where(
                    RaceEntry.jockey_id == jid,
                    RaceEntry.trainer_id == tid,
                    Race.date < before_date,
                    RaceResult.abnormality_code == 0,
                    RaceResult.finish_position.is_not(None),
                )
            )
            row = (await self.db.execute(stmt)).one_or_none()
            if row and row.total:
                combo_stats[(jid, tid)] = (int(row.wins or 0), int(row.total))
            else:
                combo_stats[(jid, tid)] = (0, 0)

        # ----------------------------------------------------------------
        # Step 4: 各騎手の単独統計を取得
        # ----------------------------------------------------------------
        jockey_stats: dict[int, tuple[int, int]] = {}  # (wins, total)
        for jid in unique_jockey_ids:
            stmt = (
                select(
                    func.count().label("total"),
                    func.sum(
                        case((RaceResult.finish_position == 1, 1), else_=0)
                    ).label("wins"),
                )
                .join(Race, RaceResult.race_id == Race.id)
                .join(
                    RaceEntry,
                    (RaceEntry.race_id == RaceResult.race_id)
                    & (RaceEntry.horse_id == RaceResult.horse_id),
                )
                .where(
                    RaceEntry.jockey_id == jid,
                    Race.date < before_date,
                    RaceResult.abnormality_code == 0,
                    RaceResult.finish_position.is_not(None),
                )
            )
            row = (await self.db.execute(stmt)).one_or_none()
            if row and row.total:
                jockey_stats[jid] = (int(row.wins or 0), int(row.total))
            else:
                jockey_stats[jid] = (0, 0)

        # ----------------------------------------------------------------
        # Step 5: スコア算出
        # ----------------------------------------------------------------
        result: dict[int, float | None] = {}
        for hid in horse_ids:
            combo = horse_combo.get(hid)
            if combo is None:
                result[hid] = None
                continue

            jid, tid = combo
            if jid is None or tid is None:
                result[hid] = None
                continue

            c_wins, c_total = combo_stats.get((jid, tid), (0, 0))
            j_wins, j_total = jockey_stats.get(jid, (0, 0))

            if c_total < MIN_COMBO_RACES:
                result[hid] = None
                continue

            combo_win_rate = c_wins / c_total
            solo_win_rate = j_wins / max(j_total, 1)
            lift = combo_win_rate - solo_win_rate
            raw = lift * LIFT_SCALE
            score = 50.0 + max(CLIP_MIN, min(CLIP_MAX, raw))
            result[hid] = round(score, 1)

        return result
