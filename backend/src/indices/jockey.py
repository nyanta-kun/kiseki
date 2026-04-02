"""騎手指数算出Agent

騎手の過去成績（勝率・連対率・上がり3F）を同surface条件で集計し、
騎乗技術の高さをスコア化する。

算出ロジック:
  1. 対象レースのエントリから騎手ID（RaceEntry.jockey_id）を取得
  2. 過去 LOOKBACK_DAYS 日間の同騎手の全成績を取得（RaceResult.jockey_id）
  3. フィルタリング: 同 surface（芝/ダ）かつ距離 ±DIST_TOLERANCE m 以内
  4. 以下の3スコアを算出:
     - 勝率スコア: wins / total * 100（0-100）
     - 連対率スコア: top2 / total * 100（0-100）
     - 上がり3F偏差スコア: 騎手のlast_3f平均が同条件平均より速ければ高スコア（0-100）
  5. 重み付け平均: 勝率 40% + 連対率 30% + 上がり3F 30%
  6. 全騎手の分布から平均50・σ=10 に正規化（偏差スコア）
  7. サンプル不足（MIN_SAMPLE=10戦未満）は SPEED_INDEX_MEAN=50.0 を返す

制約:
  - 除外・取消（abnormality_code > 0）のレースは除外
  - 騎手未登録（jockey_id=None）の馬は SPEED_INDEX_MEAN を返す
  - バッチ処理は騎手単位で集計しN+1を回避
"""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Race, RaceEntry, RaceResult
from ..utils.constants import SPEED_INDEX_MEAN, SPEED_INDEX_STD
from .base import IndexCalculator

logger = logging.getLogger(__name__)

# 過去何日を参照するか
LOOKBACK_DAYS = 730
# サンプル不足判定の最低戦数
MIN_SAMPLE = 10
# 距離許容範囲（±m）
DIST_TOLERANCE = 400
# 指数クリップ範囲
INDEX_MIN = 0.0
INDEX_MAX = 100.0

# 各スコアの重み
WEIGHT_WIN_RATE = 0.40
WEIGHT_TOP2_RATE = 0.30
WEIGHT_LAST3F = 0.30


class JockeyIndexCalculator(IndexCalculator):
    """騎手指数算出Agent。

    IndexCalculator を継承し、単一馬（calculate）と
    レース全馬バッチ（calculate_batch）の両インターフェースを提供する。
    """

    def __init__(self, db: AsyncSession) -> None:
        """初期化。

        Args:
            db: SQLAlchemy 非同期セッション
        """
        super().__init__(db)
        # 騎手統計のキャッシュ（jockey_id + surface + distance → raw_score）
        self._jockey_stats_cache: dict[tuple[int, str, int], float | None] = {}

    # ------------------------------------------------------------------
    # 公開インターフェース
    # ------------------------------------------------------------------

    async def calculate(self, race_id: int, horse_id: int) -> float:
        """単一馬の騎手指数を算出する。

        Args:
            race_id: DB の races.id（対象レース）
            horse_id: DB の horses.id

        Returns:
            騎手指数（0-100, 平均50）。データ不足または騎手未登録時は SPEED_INDEX_MEAN。
        """
        race_result = await self.db.execute(select(Race).where(Race.id == race_id))
        race = race_result.scalar_one_or_none()
        if not race:
            logger.warning(f"Race not found: race_id={race_id}")
            return SPEED_INDEX_MEAN

        entry_result = await self.db.execute(
            select(RaceEntry).where(
                and_(
                    RaceEntry.race_id == race_id,
                    RaceEntry.horse_id == horse_id,
                )
            )
        )
        entry = entry_result.scalar_one_or_none()
        if not entry or entry.jockey_id is None:
            return SPEED_INDEX_MEAN

        before_date = race.date
        surface = race.surface or ""
        distance = race.distance or 0

        raw = await self._get_jockey_raw_score(entry.jockey_id, before_date, surface, distance)
        if raw is None:
            return SPEED_INDEX_MEAN

        # 単一馬の場合、正規化に必要な集団がないため raw_score をそのまま返す
        # （calculate_batch での正規化を推奨）
        return round(max(INDEX_MIN, min(INDEX_MAX, raw)), 1)

    async def calculate_batch(self, race_id: int) -> dict[int, float]:
        """レース全馬の騎手指数を一括算出する。

        騎手単位で集計しN+1を回避する。全騎手のスコアを正規化（平均50, σ=10）して返す。

        Args:
            race_id: DB の races.id

        Returns:
            {horse_id: jockey_index} のdict。エントリが存在しない場合は空dict。
        """
        race_result = await self.db.execute(select(Race).where(Race.id == race_id))
        race = race_result.scalar_one_or_none()
        if not race:
            logger.warning(f"Race not found: race_id={race_id}")
            return {}

        entries_result = await self.db.execute(select(RaceEntry).where(RaceEntry.race_id == race_id))
        entries = entries_result.scalars().all()
        if not entries:
            return {}

        before_date = race.date
        surface = race.surface or ""
        distance = race.distance or 0

        # 騎手IDの一覧を収集（None除外）
        jockey_ids = list({e.jockey_id for e in entries if e.jockey_id is not None})

        # 騎手ごとの過去成績を一括取得
        jockey_stats = await self._get_all_jockey_stats_batch(jockey_ids, before_date, surface, distance)

        # 有効スコアのみで正規化パラメータを算出
        valid_scores = [v for v in jockey_stats.values() if v is not None]
        mean, std = self._compute_normalization_params(valid_scores)

        result: dict[int, float] = {}
        for entry in entries:
            if entry.jockey_id is None:
                result[entry.horse_id] = SPEED_INDEX_MEAN
                continue

            raw = jockey_stats.get(entry.jockey_id)
            if raw is None:
                result[entry.horse_id] = SPEED_INDEX_MEAN
            else:
                normalized = self._normalize(raw, mean, std)
                result[entry.horse_id] = round(max(INDEX_MIN, min(INDEX_MAX, normalized)), 1)

        return result

    # ------------------------------------------------------------------
    # 内部メソッド
    # ------------------------------------------------------------------

    async def _get_jockey_raw_score(
        self,
        jockey_id: int,
        before_date: str,
        surface: str,
        distance: int,
    ) -> float | None:
        """単一騎手の生スコアを算出する。

        同セッション内でキャッシュし、同一条件で2回以上呼ばれてもDBアクセスは1回。

        Args:
            jockey_id: jockeys.id
            before_date: この日付より前のレースのみ（YYYYMMDD）
            surface: 芝/ダ/障
            distance: 対象距離（m）

        Returns:
            生スコア（0-100）。サンプル不足時は None。
        """
        cache_key = (jockey_id, surface, distance)
        if cache_key in self._jockey_stats_cache:
            return self._jockey_stats_cache[cache_key]

        rows = await self._query_jockey_results(jockey_id, before_date, surface, distance)
        score = self._compute_raw_score(rows, surface, distance)
        self._jockey_stats_cache[cache_key] = score
        return score

    async def _get_all_jockey_stats_batch(
        self,
        jockey_ids: list[int],
        before_date: str,
        surface: str,
        distance: int,
    ) -> dict[int, float | None]:
        """複数騎手の生スコアを一括算出する。

        Args:
            jockey_ids: 対象 jockeys.id のリスト
            before_date: この日付より前のレース（YYYYMMDD）
            surface: 芝/ダ/障
            distance: 対象距離（m）

        Returns:
            {jockey_id: raw_score or None}
        """
        if not jockey_ids:
            return {}

        # キャッシュ済みを先にチェック
        result: dict[int, float | None] = {}
        missing_ids: list[int] = []

        for jid in jockey_ids:
            cache_key = (jid, surface, distance)
            if cache_key in self._jockey_stats_cache:
                result[jid] = self._jockey_stats_cache[cache_key]
            else:
                missing_ids.append(jid)

        if not missing_ids:
            return result

        # 未キャッシュ分を一括クエリ
        rows_all = await self._query_jockey_results_batch(missing_ids, before_date, surface, distance)

        for jid in missing_ids:
            rows = rows_all.get(jid, [])
            score = self._compute_raw_score(rows, surface, distance)
            cache_key = (jid, surface, distance)
            self._jockey_stats_cache[cache_key] = score
            result[jid] = score

        return result

    async def _query_jockey_results(
        self,
        jockey_id: int,
        before_date: str,
        surface: str,
        distance: int,
    ) -> list[Any]:
        """単一騎手の過去成績をDBから取得する。

        同 surface かつ距離 ±DIST_TOLERANCE m 以内のレースを対象とする。

        Args:
            jockey_id: jockeys.id
            before_date: この日付より前のレース（YYYYMMDD）
            surface: 馬場種別（芝/ダ/障）
            distance: 対象距離（m）

        Returns:
            [(RaceResult, Race), ...] の結果リスト
        """
        since_date = _calc_since_date(before_date, LOOKBACK_DAYS)

        stmt = (
            select(RaceResult, Race)
            .join(Race, RaceResult.race_id == Race.id)
            .where(
                RaceResult.jockey_id == jockey_id,
                Race.date >= since_date,
                Race.date < before_date,
                Race.surface == surface,
                RaceResult.finish_position.isnot(None),
                RaceResult.abnormality_code == 0,
            )
            .order_by(Race.date.desc())
        )
        result = await self.db.execute(stmt)
        return list(result.all())

    async def _query_jockey_results_batch(
        self,
        jockey_ids: list[int],
        before_date: str,
        surface: str,
        distance: int,
    ) -> dict[int, list[Any]]:
        """複数騎手の過去成績を一括取得する。

        Args:
            jockey_ids: 対象 jockeys.id のリスト
            before_date: この日付より前のレース（YYYYMMDD）
            surface: 馬場種別
            distance: 対象距離（m）

        Returns:
            {jockey_id: [(RaceResult, Race), ...]}
        """
        if not jockey_ids:
            return {}

        since_date = _calc_since_date(before_date, LOOKBACK_DAYS)

        stmt = (
            select(RaceResult, Race)
            .join(Race, RaceResult.race_id == Race.id)
            .where(
                RaceResult.jockey_id.in_(jockey_ids),
                Race.date >= since_date,
                Race.date < before_date,
                Race.surface == surface,
                RaceResult.finish_position.isnot(None),
                RaceResult.abnormality_code == 0,
            )
            .order_by(RaceResult.jockey_id, Race.date.desc())
        )
        result = await self.db.execute(stmt)
        rows = result.all()

        result_map: dict[int, list[Any]] = defaultdict(list)
        for row in rows:
            jid = row.RaceResult.jockey_id
            result_map[jid].append(row)

        return dict(result_map)

    def _compute_raw_score(
        self,
        rows: list[Any],
        target_surface: str,
        target_distance: int,
    ) -> float | None:
        """過去成績リストから騎手の生スコアを算出する。

        距離フィルタリング（±DIST_TOLERANCE m）を適用後、
        勝率・連対率・上がり3F偏差を合成する。

        Args:
            rows: [(RaceResult, Race), ...] の成績リスト
            target_surface: フィルタ済み馬場種別（クエリ済み）
            target_distance: 対象距離（m）

        Returns:
            生スコア（0-100）。MIN_SAMPLE 未満の場合は None。
        """
        # 距離フィルタを適用
        filtered = [
            row for row in rows if abs((row.Race.distance or 0) - target_distance) <= DIST_TOLERANCE
        ]

        total = len(filtered)
        if total < MIN_SAMPLE:
            return None

        wins = sum(
            1
            for row in filtered
            if row.RaceResult.finish_position is not None
            and int(row.RaceResult.finish_position) == 1
        )
        top2 = sum(
            1
            for row in filtered
            if row.RaceResult.finish_position is not None
            and int(row.RaceResult.finish_position) <= 2
        )

        win_rate_score = wins / total * 100.0
        top2_rate_score = top2 / total * 100.0
        last3f_score = self._compute_last3f_score(filtered)

        raw = (
            win_rate_score * WEIGHT_WIN_RATE
            + top2_rate_score * WEIGHT_TOP2_RATE
            + last3f_score * WEIGHT_LAST3F
        )
        return raw

    def _compute_last3f_score(self, rows: list[Any]) -> float:
        """上がり3F偏差スコアを算出する（0-100）。

        騎手の平均 last_3f が同リスト内の全体平均より速ければ高スコア。
        データが存在しない場合は 50.0 を返す。

        Args:
            rows: [(RaceResult, Race), ...] の成績リスト（距離フィルタ適用済み）

        Returns:
            上がり3Fスコア（0-100）
        """
        last3f_values = [
            float(row.RaceResult.last_3f) for row in rows if row.RaceResult.last_3f is not None
        ]
        if not last3f_values:
            return SPEED_INDEX_MEAN

        mean_jockey = statistics.mean(last3f_values)
        mean_all = statistics.mean(last3f_values)  # 同一サンプル内での相対評価
        if len(last3f_values) >= 2:
            std_all = statistics.stdev(last3f_values)
        else:
            return SPEED_INDEX_MEAN

        if std_all < 0.01:
            return SPEED_INDEX_MEAN

        # last_3f は秒数（小さいほど速い）なので符号を反転
        diff = mean_all - mean_jockey  # 正なら平均より速い
        score = (diff / std_all) * SPEED_INDEX_STD + SPEED_INDEX_MEAN
        return max(INDEX_MIN, min(INDEX_MAX, score))

    @staticmethod
    def _compute_normalization_params(scores: list[float]) -> tuple[float, float]:
        """スコアリストから正規化パラメータ（平均・標準偏差）を算出する。

        Args:
            scores: 有効な生スコアのリスト

        Returns:
            (平均, 標準偏差)。スコアが2件未満の場合は (SPEED_INDEX_MEAN, 1.0)。
        """
        if len(scores) < 2:
            return (SPEED_INDEX_MEAN, 1.0)
        mean = statistics.mean(scores)
        std = statistics.stdev(scores)
        return (mean, max(std, 0.01))

    @staticmethod
    def _normalize(raw: float, mean: float, std: float) -> float:
        """生スコアを平均50・σ=10 に正規化する。

        Args:
            raw: 生スコア
            mean: 集団の平均
            std: 集団の標準偏差

        Returns:
            正規化後スコア
        """
        return (raw - mean) / std * SPEED_INDEX_STD + SPEED_INDEX_MEAN


# ------------------------------------------------------------------
# モジュールレベルユーティリティ
# ------------------------------------------------------------------


def _calc_since_date(before_date: str, lookback_days: int) -> str:
    """YYYYMMDD 文字列から lookback_days 日前の日付文字列を算出する。

    Args:
        before_date: 基準日（YYYYMMDD）
        lookback_days: 遡る日数

    Returns:
        (基準日 - lookback_days 日) の YYYYMMDD 文字列
    """
    dt = datetime.strptime(before_date, "%Y%m%d")
    since = dt - timedelta(days=lookback_days)
    return since.strftime("%Y%m%d")
