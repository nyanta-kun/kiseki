"""巻き返し指数算出Agent

前走での不利（remarks）と着順乖離から、次走での巻き返し期待度をスコア化する。

算出ロジック:
  1. 前走の netkeiba_race_extras.remarks に不利キーワードがあるか確認
  2. 前走の calculated_indices.win_probability から期待着順を計算
       expected_position = round(n_horses × (1 - win_probability))
  3. 着順乖離 = finish_position - expected_position
  4. スコア算出:
       - 不利なし（remarks なしまたはデータなし）: 50.0（中立）
       - 出遅れ + 好走（乖離 ≤ 0）: 50.0（巻き返し候補にしない）
       - 常習出遅れ（直近5走で出遅れ2回以上）: 40.0（慢性的 → 軽減）
       - 出遅れ + 着順大幅乖離: 50.0 + min(50.0, 乖離 × 10.0 × 0.6)
       - その他の不利 + 着順乖離 > 0: 50.0 + min(50.0, 乖離 × 10.0)
  5. clip(0, 100) して返す

制約:
  - netkeiba_race_extras が未収集の場合はデータなし扱い（スコア=50）
  - 除外・取消（abnormality_code > 0）のレースは前走として使用しない
  - win_probability が取得できない場合は着順乖離の計算をスキップ（不利有無のみ反映）
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import CalculatedIndex, NetkeibaRaceExtra, Race, RaceEntry, RaceResult
from .base import IndexCalculator

logger = logging.getLogger(__name__)

# 前走の着順乖離を参照するレース数
LOOKBACK_RACES = 1
# 常習出遅れ判定に参照する過去レース数
CHRONIC_LOOKBACK = 5
# 常習認定の出遅れ回数閾値
CHRONIC_SLIPSTART_THRESHOLD = 2
# 不利あり時の 1着順乖離あたりのスコアボーナス
GAP_BONUS_PER_POSITION = 10.0
# ボーナス上限（スコア最大 50+50=100）
GAP_BONUS_MAX = 50.0
# 出遅れ時のボーナス係数（他の不利より確信度が低いため低め）
SLIPSTART_MULTIPLIER = 0.6
# 常習出遅れ時のスコア（軽減）
CHRONIC_SLIPSTART_SCORE = 40.0
# デフォルト（中立）スコア
DEFAULT_SCORE = 50.0
# 不利がない時のデフォルトスコア
INDEX_MIN = 0.0
INDEX_MAX = 100.0

# 不利キーワード（netkeiba_importer._DISADVANTAGE_KEYWORDS と同期して管理）
_DISADVANTAGE_KEYWORDS: frozenset[str] = frozenset(
    [
        "出遅れ",
        "不利",
        "S接触",
        "内に張られ",
        "外に張られ",
        "内に寄られ",
        "外に寄られ",
        "弾かれ",
        "挟まれ",
    ]
)
_SLIPSTART_KEYWORD = "出遅れ"


def _has_disadvantage(remarks: str | None) -> bool:
    """備考に不利キーワードが含まれるか確認する。

    Args:
        remarks: netkeiba_race_extras.remarks の文字列（None 可）

    Returns:
        不利キーワードが含まれる場合 True
    """
    if not remarks:
        return False
    return any(kw in remarks for kw in _DISADVANTAGE_KEYWORDS)


def _has_slipstart(remarks: str | None) -> bool:
    """備考に出遅れキーワードが含まれるか確認する。

    Args:
        remarks: netkeiba_race_extras.remarks の文字列（None 可）

    Returns:
        出遅れが含まれる場合 True
    """
    if not remarks:
        return False
    return _SLIPSTART_KEYWORD in remarks


def _compute_score(
    remarks: str | None,
    finish_position: int | None,
    win_probability: float | None,
    n_horses: int,
    is_chronic_slipstart: bool,
) -> float:
    """不利情報と着順乖離からスコアを算出する。

    Args:
        remarks: 前走備考（None の場合はデータなし扱い）
        finish_position: 前走着順（None の場合は乖離計算スキップ）
        win_probability: 前走時の単勝確率（None の場合は乖離計算スキップ）
        n_horses: 前走の出走頭数（乖離計算に使用）
        is_chronic_slipstart: 常習出遅れフラグ

    Returns:
        巻き返し指数スコア（0-100, 中立=50）
    """
    if not _has_disadvantage(remarks):
        return DEFAULT_SCORE

    is_slipstart = _has_slipstart(remarks)

    # 常習出遅れ → 慢性問題として軽減スコア
    if is_slipstart and is_chronic_slipstart:
        return CHRONIC_SLIPSTART_SCORE

    # 着順乖離の計算
    if finish_position is None or win_probability is None or n_horses <= 0:
        # データ不足時は不利ありだが乖離不明 → 小幅ボーナス
        return DEFAULT_SCORE + 10.0

    expected_position = round(n_horses * (1.0 - win_probability))
    expected_position = max(1, min(n_horses, expected_position))
    position_gap = finish_position - expected_position

    # 好走した場合（期待通りかそれ以上）→ 巻き返し候補にしない
    if position_gap <= 0:
        return DEFAULT_SCORE

    raw_bonus = min(GAP_BONUS_MAX, position_gap * GAP_BONUS_PER_POSITION)
    if is_slipstart:
        raw_bonus *= SLIPSTART_MULTIPLIER

    score = DEFAULT_SCORE + raw_bonus
    return round(max(INDEX_MIN, min(INDEX_MAX, score)), 1)


class ReboundIndexCalculator(IndexCalculator):
    """巻き返し指数算出Agent。

    IndexCalculator を継承し、単一馬（calculate）と
    レース全馬バッチ（calculate_batch）の両インターフェースを提供する。
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
        """単一馬の巻き返し指数を算出する。

        Args:
            race_id: DB の races.id（対象レース）
            horse_id: DB の horses.id

        Returns:
            巻き返し指数（0-100, 中立50）。データ不足時は DEFAULT_SCORE。
        """
        race_result = await self.db.execute(select(Race).where(Race.id == race_id))
        race = race_result.scalar_one_or_none()
        if not race:
            return DEFAULT_SCORE

        prev_data = await self._get_prev_data_for_horse(horse_id, race.date, race_id)
        chronic = await self._is_chronic_slipstart(horse_id, race.date, race_id)
        return self._compute_from_prev_data(prev_data, chronic)

    async def calculate_batch(self, race_id: int) -> dict[int, float | None]:
        """レース全馬の巻き返し指数を一括算出する。

        N+1 を回避するため、全馬のデータを単一または少数のクエリで取得する。

        Args:
            race_id: DB の races.id

        Returns:
            {horse_id: rebound_index} の dict。
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

        # 前走データ・常習出遅れフラグを一括取得
        prev_data_map = await self._get_prev_data_batch(horse_ids, race.date, race_id)
        chronic_map = await self._get_chronic_slipstart_batch(horse_ids, race.date, race_id)

        result: dict[int, float | None] = {}
        for entry in entries:
            hid = entry.horse_id
            prev_data = prev_data_map.get(hid)
            chronic = chronic_map.get(hid, False)
            result[hid] = self._compute_from_prev_data(prev_data, chronic)

        return result

    # ------------------------------------------------------------------
    # 内部メソッド: データ取得
    # ------------------------------------------------------------------

    async def _get_prev_data_for_horse(
        self, horse_id: int, before_date: str, exclude_race_id: int
    ) -> dict[str, Any] | None:
        """単一馬の前走データを取得する。

        Args:
            horse_id: horses.id
            before_date: この日付より前のレースのみ（YYYYMMDD）
            exclude_race_id: 対象レースは除外

        Returns:
            {remarks, finish_position, win_probability, n_horses} または None
        """
        stmt = (
            select(RaceResult, Race)
            .join(Race, RaceResult.race_id == Race.id)
            .where(
                RaceResult.horse_id == horse_id,
                Race.date < before_date,
                RaceResult.race_id != exclude_race_id,
                RaceResult.abnormality_code == 0,
            )
            .order_by(Race.date.desc())
            .limit(1)
        )
        row = (await self.db.execute(stmt)).first()
        if row is None:
            return None

        return await self._enrich_prev_row(row.RaceResult, row.Race)

    async def _get_prev_data_batch(
        self, horse_ids: list[int], before_date: str, exclude_race_id: int
    ) -> dict[int, dict[str, Any]]:
        """複数馬の前走データを一括取得する。

        Args:
            horse_ids: 対象 horses.id のリスト
            before_date: この日付より前のレース（YYYYMMDD）
            exclude_race_id: 対象レースは除外

        Returns:
            {horse_id: {remarks, finish_position, win_probability, n_horses}}
        """
        if not horse_ids:
            return {}

        stmt = (
            select(RaceResult, Race)
            .join(Race, RaceResult.race_id == Race.id)
            .where(
                RaceResult.horse_id.in_(horse_ids),
                Race.date < before_date,
                RaceResult.race_id != exclude_race_id,
                RaceResult.abnormality_code == 0,
            )
            .order_by(RaceResult.horse_id, Race.date.desc())
        )
        rows = (await self.db.execute(stmt)).all()

        # horse_id ごとに最新1件のみ使用
        seen: set[int] = set()
        prev_rows: dict[int, tuple[RaceResult, Race]] = {}
        for row in rows:
            hid = row.RaceResult.horse_id
            if hid not in seen:
                prev_rows[hid] = (row.RaceResult, row.Race)
                seen.add(hid)

        if not prev_rows:
            return {}

        # 前走レースID → (RaceResult, Race) のマップ
        prev_race_ids = [race.id for _, race in prev_rows.values()]

        # remarks を一括取得
        extras_result = await self.db.execute(
            select(NetkeibaRaceExtra).where(
                NetkeibaRaceExtra.race_id.in_(prev_race_ids)
            )
        )
        extras_map: dict[tuple[int, int], str | None] = {
            (e.race_id, e.horse_id): e.remarks for e in extras_result.scalars().all()
        }

        # win_probability を一括取得（最新バージョン）
        ci_result = await self.db.execute(
            select(CalculatedIndex).where(
                CalculatedIndex.race_id.in_(prev_race_ids)
            )
        )
        # 同一 (race_id, horse_id) に複数バージョンある場合は最新を使用
        ci_map: dict[tuple[int, int], float] = {}
        for ci in ci_result.scalars().all():
            key = (ci.race_id, ci.horse_id)
            if key not in ci_map or (ci.win_probability is not None):
                if ci.win_probability is not None:
                    ci_map[key] = float(ci.win_probability)

        # 出走頭数を一括取得
        n_horses_result = await self.db.execute(
            select(RaceEntry.race_id, RaceEntry.horse_id).where(
                RaceEntry.race_id.in_(prev_race_ids)
            )
        )
        n_horses_map: dict[int, int] = defaultdict(int)
        for re in n_horses_result.all():
            n_horses_map[re.race_id] += 1

        # まとめる
        result: dict[int, dict[str, Any]] = {}
        for horse_id, (prev_result, prev_race) in prev_rows.items():
            key = (prev_race.id, horse_id)
            result[horse_id] = {
                "remarks": extras_map.get(key),
                "finish_position": prev_result.finish_position,
                "win_probability": ci_map.get(key),
                "n_horses": n_horses_map.get(prev_race.id, 0),
            }

        return result

    async def _is_chronic_slipstart(
        self, horse_id: int, before_date: str, exclude_race_id: int
    ) -> bool:
        """直近 CHRONIC_LOOKBACK 走で出遅れが CHRONIC_SLIPSTART_THRESHOLD 回以上あるか確認する。

        Args:
            horse_id: horses.id
            before_date: この日付より前（YYYYMMDD）
            exclude_race_id: 対象レースは除外

        Returns:
            常習出遅れの場合 True
        """
        stmt = (
            select(RaceResult.race_id)
            .join(Race, RaceResult.race_id == Race.id)
            .where(
                RaceResult.horse_id == horse_id,
                Race.date < before_date,
                RaceResult.race_id != exclude_race_id,
                RaceResult.abnormality_code == 0,
            )
            .order_by(Race.date.desc())
            .limit(CHRONIC_LOOKBACK)
        )
        race_ids = (await self.db.execute(stmt)).scalars().all()
        if not race_ids:
            return False

        extras_result = await self.db.execute(
            select(NetkeibaRaceExtra.remarks).where(
                NetkeibaRaceExtra.race_id.in_(race_ids),
                NetkeibaRaceExtra.horse_id == horse_id,
            )
        )
        slipstart_count = sum(
            1 for (r,) in extras_result.all() if r and _SLIPSTART_KEYWORD in r
        )
        return slipstart_count >= CHRONIC_SLIPSTART_THRESHOLD

    async def _get_chronic_slipstart_batch(
        self, horse_ids: list[int], before_date: str, exclude_race_id: int
    ) -> dict[int, bool]:
        """複数馬の常習出遅れフラグを一括取得する。

        Args:
            horse_ids: 対象 horses.id のリスト
            before_date: この日付より前（YYYYMMDD）
            exclude_race_id: 対象レースは除外

        Returns:
            {horse_id: is_chronic_slipstart}
        """
        if not horse_ids:
            return {}

        stmt = (
            select(RaceResult.horse_id, RaceResult.race_id)
            .join(Race, RaceResult.race_id == Race.id)
            .where(
                RaceResult.horse_id.in_(horse_ids),
                Race.date < before_date,
                RaceResult.race_id != exclude_race_id,
                RaceResult.abnormality_code == 0,
            )
            .order_by(RaceResult.horse_id, Race.date.desc())
        )
        rows = (await self.db.execute(stmt)).all()

        # horse_id ごとに最大 CHRONIC_LOOKBACK 件のレースIDを収集
        horse_race_map: dict[int, list[int]] = defaultdict(list)
        count_map: dict[int, int] = defaultdict(int)
        for row in rows:
            hid = row.horse_id
            if count_map[hid] < CHRONIC_LOOKBACK:
                horse_race_map[hid].append(row.race_id)
                count_map[hid] += 1

        if not horse_race_map:
            return {}

        all_race_ids = [rid for rids in horse_race_map.values() for rid in rids]

        extras_result = await self.db.execute(
            select(NetkeibaRaceExtra.race_id, NetkeibaRaceExtra.horse_id, NetkeibaRaceExtra.remarks)
            .where(
                NetkeibaRaceExtra.race_id.in_(all_race_ids),
                NetkeibaRaceExtra.horse_id.in_(horse_ids),
            )
        )
        # (race_id, horse_id) → remarks
        extras_lookup: dict[tuple[int, int], str | None] = {
            (row.race_id, row.horse_id): row.remarks for row in extras_result.all()
        }

        result: dict[int, bool] = {}
        for hid, race_ids in horse_race_map.items():
            slipstart_count = sum(
                1
                for rid in race_ids
                if _has_slipstart(extras_lookup.get((rid, hid)))
            )
            result[hid] = slipstart_count >= CHRONIC_SLIPSTART_THRESHOLD

        return result

    async def _enrich_prev_row(
        self, prev_result: RaceResult, prev_race: Race
    ) -> dict[str, Any]:
        """前走の RaceResult + Race から remarks / win_probability / n_horses を補完する。

        Args:
            prev_result: 前走の RaceResult
            prev_race: 前走の Race

        Returns:
            {remarks, finish_position, win_probability, n_horses}
        """
        extras_result = await self.db.execute(
            select(NetkeibaRaceExtra).where(
                NetkeibaRaceExtra.race_id == prev_race.id,
                NetkeibaRaceExtra.horse_id == prev_result.horse_id,
            )
        )
        extra = extras_result.scalar_one_or_none()

        ci_result = await self.db.execute(
            select(CalculatedIndex).where(
                CalculatedIndex.race_id == prev_race.id,
                CalculatedIndex.horse_id == prev_result.horse_id,
                CalculatedIndex.win_probability.is_not(None),
            )
            .order_by(CalculatedIndex.version.desc())
            .limit(1)
        )
        ci = ci_result.scalar_one_or_none()

        n_horses_result = await self.db.execute(
            select(RaceEntry).where(RaceEntry.race_id == prev_race.id)
        )
        n_horses = len(n_horses_result.scalars().all())

        return {
            "remarks": extra.remarks if extra else None,
            "finish_position": prev_result.finish_position,
            "win_probability": float(ci.win_probability) if ci and ci.win_probability else None,
            "n_horses": n_horses,
        }

    # ------------------------------------------------------------------
    # 内部メソッド: スコア算出
    # ------------------------------------------------------------------

    def _compute_from_prev_data(
        self, prev_data: dict[str, Any] | None, is_chronic_slipstart: bool
    ) -> float | None:
        """前走データからスコアを算出する。

        Args:
            prev_data: {remarks, finish_position, win_probability, n_horses} または None
            is_chronic_slipstart: 常習出遅れフラグ

        Returns:
            巻き返し指数スコア（0-100）。前走データなし時は None。
        """
        if prev_data is None:
            return None

        return _compute_score(
            remarks=prev_data.get("remarks"),
            finish_position=prev_data.get("finish_position"),
            win_probability=prev_data.get("win_probability"),
            n_horses=prev_data.get("n_horses", 0),
            is_chronic_slipstart=is_chronic_slipstart,
        )
