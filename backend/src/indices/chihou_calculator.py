"""地方競馬 指数算出モジュール

UmaConn経由で取得した地方競馬データから以下の指数を算出し、
chihou.calculated_indices テーブルへ保存する。

算出指数:
  - speed_index  : スピード指数（過去レースでのフィールド内相対タイム評価）
  - last3f_index : 後3ハロン指数（末脚の相対評価）
  - jockey_index : 騎手指数（過去180日の勝率・連対率）
  - rotation_index: ローテーション指数（前走間隔 + 前走着順）
  - composite_index: 総合指数（加重平均 → Softmax確率換算）

JRAシステムとの主な差異:
  - RacecourseFeatures なし → course_aptitude は未実装
  - netkeiba remarks なし → rebound は未実装
  - sekito.anagusa なし → anagusa は未実装
  - 調教データなし → training は未実装
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.chihou_models import (
    ChihouCalculatedIndex,
    ChihouRace,
    ChihouRaceEntry,
    ChihouRaceResult,
)

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# 定数
# -----------------------------------------------------------------------

CHIHOU_COMPOSITE_VERSION = 1

# 指数デフォルト（データ不足時）
INDEX_NEUTRAL = 50.0
INDEX_MIN = 0.0
INDEX_MAX = 100.0

# スピード指数
SPEED_LOOKBACK = 10       # 過去何戦参照するか
SPEED_WEIGHT_DECAY = 0.8  # 加重減衰率
SPEED_MIN_FIELD = 3       # フィールド統計に必要な最低頭数
BASE_WEIGHT = 55.0
WEIGHT_CORR_PER_KG = 0.5  # 斤量1kgあたり0.5秒補正

# 後3ハロン指数
L3F_LOOKBACK = 10
L3F_WEIGHT_DECAY = 0.8
L3F_MIN_FIELD = 3

# 騎手指数
JOCKEY_LOOKBACK_DAYS = 180
JOCKEY_MIN_RIDES = 5

# ローテーション指数
_INTERVAL_SCORE: list[tuple[int, int, float]] = [
    # (min_days, max_days, score)
    (0,   7,  20.0),
    (8,  14,  40.0),
    (15, 21,  70.0),
    (22, 35,  80.0),
    (36, 56,  70.0),
    (57, 90,  60.0),
    (91, 180, 50.0),
    (181, 9999, 35.0),
]

_FINISH_BONUS = {1: 15.0, 2: 10.0, 3: 7.0, 4: 3.0, 5: 3.0}

# 総合指数の重み
COMPOSITE_WEIGHTS = {
    "speed":    0.40,
    "last3f":   0.25,
    "jockey":   0.20,
    "rotation": 0.15,
}

# Softmax 温度パラメータ
SOFTMAX_TEMPERATURE = 10.0


# -----------------------------------------------------------------------
# ヘルパー
# -----------------------------------------------------------------------

def _clip(v: float, lo: float = INDEX_MIN, hi: float = INDEX_MAX) -> float:
    """値を [lo, hi] にクリップする。"""
    return max(lo, min(hi, v))


def _date_to_str(d: Any) -> str:
    """datetime / date / str を YYYYMMDD 文字列に変換する。"""
    if isinstance(d, str):
        return d[:8]
    return d.strftime("%Y%m%d")


def _days_between(date_a: str, date_b: str) -> int:
    """YYYYMMDD 文字列 2 つ間の日数差（絶対値）を返す。"""
    da = datetime.strptime(date_a, "%Y%m%d")
    db = datetime.strptime(date_b, "%Y%m%d")
    return abs((da - db).days)


def _zscore_to_index(z: float) -> float:
    """z スコアを 平均50・σ=10 の指数に変換してクリップする。"""
    return _clip(z * 10.0 + 50.0)


def _weighted_avg(scores: list[float], decay: float) -> float:
    """減衰加重平均を計算する（スコアは新しい順に並んでいること）。"""
    if not scores:
        return INDEX_NEUTRAL
    total_w = 0.0
    total_v = 0.0
    for i, s in enumerate(scores):
        w = decay ** i
        total_w += w
        total_v += w * s
    return total_v / total_w


def _softmax(values: list[float], temperature: float) -> list[float]:
    """Softmax 変換を返す。"""
    if not values:
        return []
    shifted = [v / temperature for v in values]
    max_v = max(shifted)
    exps = [math.exp(v - max_v) for v in shifted]
    total = sum(exps)
    return [e / total for e in exps]


def _harville_place_probs(win_probs: list[float]) -> list[float]:
    """Harville モデルで各馬の複勝確率（上位3着以内）を算出する。

    P(i が top3 に入る) = P(i が1着) + P(i が2着) + P(i が3着)
    Harville モデルでは、k 着目を求める際に
    「それより前に着順が確定した馬を除いた残余確率で比例配分」する。

    sum(place_probs) ≈ 3.0（3頭が入着するため）。
    """
    n = len(win_probs)
    if n == 0:
        return []
    if n <= 3:
        return [1.0] * n

    result = []
    for i in range(n):
        pi = win_probs[i]
        prob = pi  # 1着

        # 2着: horse j が1着、horse i が2着
        for j in range(n):
            if j == i:
                continue
            pj = win_probs[j]
            rem1 = 1.0 - pj
            if rem1 < 1e-9:
                continue
            prob += pj * (pi / rem1)

        # 3着: horse j が1着、horse k が2着、horse i が3着
        for j in range(n):
            if j == i:
                continue
            pj = win_probs[j]
            rem1 = 1.0 - pj
            if rem1 < 1e-9:
                continue
            for k in range(n):
                if k == i or k == j:
                    continue
                pk = win_probs[k]
                rem2 = rem1 - pk
                if rem2 < 1e-9:
                    continue
                prob += pj * (pk / rem1) * (pi / rem2)

        result.append(min(1.0, max(0.0, prob)))
    return result


# -----------------------------------------------------------------------
# メインクラス
# -----------------------------------------------------------------------

class ChihouIndexCalculator:
    """地方競馬 指数算出クラス。

    一つの AsyncSession を受け取り、レース単位でバッチ算出する。
    各指数は独立して算出され、最後に composite_index と確率に合成する。
    """

    def __init__(self, db: AsyncSession) -> None:
        """初期化。

        Args:
            db: SQLAlchemy 非同期セッション
        """
        self.db = db

    # ===================================================================
    # 公開 API
    # ===================================================================

    async def calculate_and_save(self, race_id: int) -> dict[str, Any]:
        """指定レースの全馬指数を算出して DB に upsert する。

        Args:
            race_id: chihou.races.id

        Returns:
            {"saved": N, "skipped": M, "errors": K}
        """
        # レース情報取得
        race_row = await self.db.execute(select(ChihouRace).where(ChihouRace.id == race_id))
        race = race_row.scalar_one_or_none()
        if not race:
            logger.warning("chihou race not found: race_id=%d", race_id)
            return {"saved": 0, "skipped": 0, "errors": 1}

        # エントリ取得
        entry_rows = await self.db.execute(
            select(ChihouRaceEntry).where(ChihouRaceEntry.race_id == race_id)
        )
        entries = entry_rows.scalars().all()
        if not entries:
            logger.info("no entries for race_id=%d", race_id)
            return {"saved": 0, "skipped": 0, "errors": 0}

        race_date = _date_to_str(race.date)

        # 各指数をバッチ算出
        speed_map   = await self._speed_batch(race_id, race, entries)
        last3f_map  = await self._last3f_batch(race_id, race, entries)
        jockey_map  = await self._jockey_batch(race_date, entries)
        rotation_map = await self._rotation_batch(race_date, entries)

        # 総合指数・確率算出
        composite_inputs: list[tuple[int, float]] = []
        for entry in entries:
            hid = entry.horse_id
            s = speed_map.get(hid, INDEX_NEUTRAL)
            last3f = last3f_map.get(hid, INDEX_NEUTRAL)
            j = jockey_map.get(hid, INDEX_NEUTRAL)
            r = rotation_map.get(hid, INDEX_NEUTRAL)
            comp = (
                COMPOSITE_WEIGHTS["speed"]    * s
                + COMPOSITE_WEIGHTS["last3f"]   * last3f
                + COMPOSITE_WEIGHTS["jockey"]   * j
                + COMPOSITE_WEIGHTS["rotation"] * r
            )
            composite_inputs.append((hid, _clip(comp)))

        # Softmax で単勝確率を推定
        comp_values   = [v for _, v in composite_inputs]
        win_probs   = _softmax(comp_values, SOFTMAX_TEMPERATURE)
        # 複勝確率: Harville モデルで上位3着以内の確率を算出
        place_probs = _harville_place_probs(win_probs)

        # upsert
        values = []
        for i, (hid, comp) in enumerate(composite_inputs):
            values.append({
                "race_id":         race_id,
                "horse_id":        hid,
                "version":         CHIHOU_COMPOSITE_VERSION,
                "speed_index":     speed_map.get(hid, INDEX_NEUTRAL),
                "last3f_index":    last3f_map.get(hid, INDEX_NEUTRAL),
                "jockey_index":    jockey_map.get(hid, INDEX_NEUTRAL),
                "rotation_index":  rotation_map.get(hid, INDEX_NEUTRAL),
                "composite_index": comp,
                "win_probability": win_probs[i],
                "place_probability": min(1.0, place_probs[i]),
                "calculated_at":   datetime.utcnow(),
            })

        if not values:
            return {"saved": 0, "skipped": 0, "errors": 0}

        stmt = (
            pg_insert(ChihouCalculatedIndex)
            .values(values)
            .on_conflict_do_update(
                constraint="uq_chihou_calc_idx_race_horse_ver",
                set_={
                    "speed_index":      pg_insert(ChihouCalculatedIndex).excluded.speed_index,
                    "last3f_index":     pg_insert(ChihouCalculatedIndex).excluded.last3f_index,
                    "jockey_index":     pg_insert(ChihouCalculatedIndex).excluded.jockey_index,
                    "rotation_index":   pg_insert(ChihouCalculatedIndex).excluded.rotation_index,
                    "composite_index":  pg_insert(ChihouCalculatedIndex).excluded.composite_index,
                    "win_probability":  pg_insert(ChihouCalculatedIndex).excluded.win_probability,
                    "place_probability": pg_insert(ChihouCalculatedIndex).excluded.place_probability,
                    "calculated_at":    pg_insert(ChihouCalculatedIndex).excluded.calculated_at,
                },
            )
        )
        await self.db.execute(stmt)
        logger.info("chihou indices saved: race_id=%d, horses=%d", race_id, len(values))
        return {"saved": len(values), "skipped": 0, "errors": 0}

    # ===================================================================
    # スピード指数
    # ===================================================================

    async def _speed_batch(
        self,
        race_id: int,
        race: ChihouRace,
        entries: list[ChihouRaceEntry],
    ) -> dict[int, float]:
        """全エントリのスピード指数を一括算出する。

        アルゴリズム:
          1. 各馬の過去 SPEED_LOOKBACK 戦を取得
          2. 各過去レースでのフィールド（全出走馬）の finish_time の平均・標準偏差を算出
          3. フィールド内 z-score = (mean_time - horse_time) / std_time
             （タイムが短いほど高スコア）
          4. 斤量補正: (負担重量 - BASE_WEIGHT) × WEIGHT_CORR_PER_KG 秒を加算
          5. z スコアを指数化して加重平均
        """
        race_date = _date_to_str(race.date)
        horse_ids = [e.horse_id for e in entries]

        # 各馬の過去結果を一括取得（N+1回避）
        past_results = await self._get_past_results_batch(
            horse_ids, race_date, exclude_race_id=race_id, limit_per_horse=SPEED_LOOKBACK
        )
        if not past_results:
            return {}

        # 関係するレースIDを収集してフィールド統計を一括取得
        involved_race_ids = list({r["race_id"] for r in past_results})
        field_stats = await self._get_field_time_stats(involved_race_ids)

        # 馬ごとにスコアを集計
        result: dict[int, float] = {}
        from collections import defaultdict
        horse_scores: dict[int, list[float]] = defaultdict(list)

        for row in past_results:
            hid = row["horse_id"]
            rid = row["race_id"]
            ft = row["finish_time"]
            wc = row["weight_carried"]
            ab = row["abnormality_code"]

            if ab and ab > 0:
                continue
            if ft is None:
                continue

            stats = field_stats.get(rid)
            if not stats or stats["cnt"] < SPEED_MIN_FIELD:
                continue

            mean_t = stats["mean"]
            std_t  = stats["std"]
            if std_t is None or std_t < 0.1:
                continue

            # 斤量補正（重い = タイムを短く補正）
            ft_float = float(ft)
            if wc is not None:
                ft_float += (float(wc) - BASE_WEIGHT) * WEIGHT_CORR_PER_KG

            z = (mean_t - ft_float) / std_t
            horse_scores[hid].append(_zscore_to_index(z))

        for hid, scores in horse_scores.items():
            result[hid] = _weighted_avg(scores, SPEED_WEIGHT_DECAY)

        return result

    # ===================================================================
    # 後3ハロン指数
    # ===================================================================

    async def _last3f_batch(
        self,
        race_id: int,
        race: ChihouRace,
        entries: list[ChihouRaceEntry],
    ) -> dict[int, float]:
        """全エントリの後3ハロン指数を一括算出する。

        フィールド内の last_3f の z-score を計算して指数化する。
        末脚が速いほど高スコア（last_3f が小さいほど良い）。
        """
        race_date = _date_to_str(race.date)
        horse_ids = [e.horse_id for e in entries]

        past_results = await self._get_past_results_batch(
            horse_ids, race_date, exclude_race_id=race_id, limit_per_horse=L3F_LOOKBACK
        )
        if not past_results:
            return {}

        involved_race_ids = list({r["race_id"] for r in past_results})
        field_stats = await self._get_field_l3f_stats(involved_race_ids)

        from collections import defaultdict
        horse_scores: dict[int, list[float]] = defaultdict(list)

        for row in past_results:
            hid = row["horse_id"]
            rid = row["race_id"]
            l3f = row["last_3f"]
            ab  = row["abnormality_code"]

            if ab and ab > 0:
                continue
            if l3f is None:
                continue

            stats = field_stats.get(rid)
            if not stats or stats["cnt"] < L3F_MIN_FIELD:
                continue

            mean_l = stats["mean"]
            std_l  = stats["std"]
            if std_l is None or std_l < 0.05:
                continue

            z = (mean_l - float(l3f)) / std_l
            horse_scores[hid].append(_zscore_to_index(z))

        result: dict[int, float] = {}
        for hid, scores in horse_scores.items():
            result[hid] = _weighted_avg(scores, L3F_WEIGHT_DECAY)

        return result

    # ===================================================================
    # 騎手指数
    # ===================================================================

    async def _jockey_batch(
        self,
        race_date: str,
        entries: list[ChihouRaceEntry],
    ) -> dict[int, float]:
        """全エントリの騎手指数を一括算出する。

        過去 JOCKEY_LOOKBACK_DAYS 日間の勝率・連対率（複勝率）から算出する。
        """
        jockey_to_horses: dict[int, list[int]] = {}
        for e in entries:
            if e.jockey_id is not None:
                jockey_to_horses.setdefault(e.jockey_id, []).append(e.horse_id)

        if not jockey_to_horses:
            return {}

        jockey_ids = list(jockey_to_horses.keys())
        cutoff_date = (
            datetime.strptime(race_date, "%Y%m%d") - timedelta(days=JOCKEY_LOOKBACK_DAYS)
        ).strftime("%Y%m%d")

        # 騎手別の勝利数・連対数・出走数を一括集計
        rows = await self.db.execute(
            select(
                ChihouRaceResult.jockey_id,
                func.count().label("rides"),
                func.sum(
                    case((ChihouRaceResult.finish_position == 1, 1), else_=0)
                ).label("wins"),
                func.sum(
                    case((ChihouRaceResult.finish_position <= 3, 1), else_=0)
                ).label("places"),
            )
            .join(ChihouRace, ChihouRace.id == ChihouRaceResult.race_id)
            .where(
                and_(
                    ChihouRaceResult.jockey_id.in_(jockey_ids),
                    ChihouRace.date >= cutoff_date,
                    ChihouRace.date < race_date,
                    ChihouRaceResult.abnormality_code.in_([0, None]),
                    ChihouRaceResult.finish_position.is_not(None),
                )
            )
            .group_by(ChihouRaceResult.jockey_id)
        )

        jockey_scores: dict[int, float] = {}
        for row in rows.mappings():
            jid  = row["jockey_id"]
            rides = int(row["rides"] or 0)
            wins  = int(row["wins"] or 0)
            places = int(row["places"] or 0)

            if rides < JOCKEY_MIN_RIDES:
                continue

            win_rate   = wins   / rides
            place_rate = places / rides

            # 勝率を 0-100 にスケール（地方競馬の平均勝率 ~8% 前後を基準に）
            # 勝率0% → 30, 10% → 50, 20% → 70, 30% → 90 の線形補間
            win_score   = _clip(win_rate   * 200.0 + 30.0)
            place_score = _clip(place_rate * 100.0 + 20.0)

            # 勝率60% + 連対率40%
            jockey_scores[jid] = _clip(win_score * 0.6 + place_score * 0.4)

        result: dict[int, float] = {}
        for jid, hids in jockey_to_horses.items():
            score = jockey_scores.get(jid, INDEX_NEUTRAL)
            for hid in hids:
                result[hid] = score

        return result

    # ===================================================================
    # ローテーション指数
    # ===================================================================

    async def _rotation_batch(
        self,
        race_date: str,
        entries: list[ChihouRaceEntry],
    ) -> dict[int, float]:
        """全エントリのローテーション指数を一括算出する。

        前走間隔（日数）と前走着順からスコアを決定する。
        """
        horse_ids = [e.horse_id for e in entries]

        # 各馬の直前レース結果を取得（race_date より前の最新1戦）
        subq = (
            select(
                ChihouRaceResult.horse_id,
                func.max(ChihouRace.date).label("last_date"),
            )
            .join(ChihouRace, ChihouRace.id == ChihouRaceResult.race_id)
            .where(
                and_(
                    ChihouRaceResult.horse_id.in_(horse_ids),
                    ChihouRace.date < race_date,
                    ChihouRaceResult.abnormality_code == 0,
                )
            )
            .group_by(ChihouRaceResult.horse_id)
            .subquery()
        )

        rows = await self.db.execute(
            select(
                ChihouRaceResult.horse_id,
                ChihouRace.date.label("prev_date"),
                ChihouRaceResult.finish_position,
            )
            .join(ChihouRace, ChihouRace.id == ChihouRaceResult.race_id)
            .join(
                subq,
                and_(
                    subq.c.horse_id == ChihouRaceResult.horse_id,
                    subq.c.last_date == ChihouRace.date,
                ),
            )
        )

        result: dict[int, float] = {}
        for row in rows.mappings():
            hid       = row["horse_id"]
            prev_date = _date_to_str(row["prev_date"])
            finish    = row["finish_position"]
            days      = _days_between(race_date, prev_date)

            # 間隔スコア
            interval_score = 35.0
            for lo, hi, sc in _INTERVAL_SCORE:
                if lo <= days <= hi:
                    interval_score = sc
                    break

            # 前走着順ボーナス
            bonus = _FINISH_BONUS.get(finish, 0.0) if finish else 0.0
            result[hid] = _clip(interval_score + bonus)

        return result

    # ===================================================================
    # 共通クエリヘルパー
    # ===================================================================

    async def _get_past_results_batch(
        self,
        horse_ids: list[int],
        before_date: str,
        exclude_race_id: int,
        limit_per_horse: int,
    ) -> list[dict[str, Any]]:
        """複数馬の過去成績を一括取得する（N+1回避）。

        Args:
            horse_ids: 対象馬IDリスト
            before_date: この日付より前のレース（YYYYMMDD）
            exclude_race_id: 除外するレースID（通常は当該レース）
            limit_per_horse: 馬ごとの最大取得件数

        Returns:
            [{"horse_id", "race_id", "finish_time", "last_3f",
              "weight_carried", "abnormality_code"}, ...]
        """
        if not horse_ids:
            return []

        # RANK() OVER (PARTITION BY horse_id ORDER BY date DESC) で直近N件
        from sqlalchemy import text

        q = text("""
            WITH ranked AS (
                SELECT
                    rr.horse_id,
                    rr.race_id,
                    r.date,
                    rr.finish_time,
                    rr.last_3f,
                    rr.weight_carried,
                    rr.abnormality_code,
                    ROW_NUMBER() OVER (
                        PARTITION BY rr.horse_id
                        ORDER BY r.date DESC, rr.race_id DESC
                    ) AS rn
                FROM chihou.race_results rr
                JOIN chihou.races r ON r.id = rr.race_id
                WHERE rr.horse_id = ANY(:horse_ids)
                  AND r.date < :before_date
                  AND rr.race_id != :exclude_race_id
            )
            SELECT horse_id, race_id, date, finish_time, last_3f,
                   weight_carried, abnormality_code
            FROM ranked
            WHERE rn <= :limit
            ORDER BY horse_id, date DESC
        """)

        rows = await self.db.execute(
            q,
            {
                "horse_ids":       horse_ids,
                "before_date":     before_date,
                "exclude_race_id": exclude_race_id,
                "limit":           limit_per_horse,
            },
        )
        return [dict(r._mapping) for r in rows]

    async def _get_field_time_stats(
        self, race_ids: list[int]
    ) -> dict[int, dict[str, Any]]:
        """指定レースのフィールド finish_time の平均・標準偏差・件数を取得する。"""
        if not race_ids:
            return {}

        rows = await self.db.execute(
            select(
                ChihouRaceResult.race_id,
                func.avg(ChihouRaceResult.finish_time).label("mean"),
                func.stddev_pop(ChihouRaceResult.finish_time).label("std"),
                func.count().label("cnt"),
            )
            .where(
                and_(
                    ChihouRaceResult.race_id.in_(race_ids),
                    ChihouRaceResult.finish_time.is_not(None),
                    ChihouRaceResult.abnormality_code == 0,
                )
            )
            .group_by(ChihouRaceResult.race_id)
        )

        result: dict[int, dict[str, Any]] = {}
        for row in rows.mappings():
            result[int(row["race_id"])] = {
                "mean": float(row["mean"]) if row["mean"] is not None else None,
                "std":  float(row["std"])  if row["std"]  is not None else None,
                "cnt":  int(row["cnt"]),
            }
        return result

    async def _get_field_l3f_stats(
        self, race_ids: list[int]
    ) -> dict[int, dict[str, Any]]:
        """指定レースのフィールド last_3f の平均・標準偏差・件数を取得する。"""
        if not race_ids:
            return {}

        rows = await self.db.execute(
            select(
                ChihouRaceResult.race_id,
                func.avg(ChihouRaceResult.last_3f).label("mean"),
                func.stddev_pop(ChihouRaceResult.last_3f).label("std"),
                func.count().label("cnt"),
            )
            .where(
                and_(
                    ChihouRaceResult.race_id.in_(race_ids),
                    ChihouRaceResult.last_3f.is_not(None),
                    ChihouRaceResult.abnormality_code == 0,
                )
            )
            .group_by(ChihouRaceResult.race_id)
        )

        result: dict[int, dict[str, Any]] = {}
        for row in rows.mappings():
            result[int(row["race_id"])] = {
                "mean": float(row["mean"]) if row["mean"] is not None else None,
                "std":  float(row["std"])  if row["std"]  is not None else None,
                "cnt":  int(row["cnt"]),
            }
        return result
