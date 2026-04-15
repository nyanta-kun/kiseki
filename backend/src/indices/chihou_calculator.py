"""地方競馬 指数算出モジュール

UmaConn経由で取得した地方競馬データから以下の指数を算出し、
chihou.calculated_indices テーブルへ保存する。

算出指数:
  - speed_index  : スピード指数（コース・距離・馬場状態別基準タイムとの比較）
  - last3f_index : 後3ハロン指数（コース・距離別基準後3Fタイムとの比較）
  - jockey_index : 騎手指数（競馬場別の過去180日勝率・連対率）
  - rotation_index: ローテーション指数（前走間隔 + 前走着順）
  - place_ev_index: 複勝期待値指数（複勝確率×推定複勝オッズ、EV>1.0で期待値プラス）
  - composite_index: 総合指数（加重平均 → Softmax確率換算）

JRAシステムとの主な差異:
  - RacecourseFeatures なし → course_aptitude は未実装
  - netkeiba remarks なし → rebound は未実装
  - sekito.anagusa なし → anagusa は未実装
  - 調教データなし → training は未実装

v2 変更点（CHIHOU_COMPOSITE_VERSION=2）:
  - ばんえい競馬（course='83'）を除外
  - スピード指数: コース・距離・馬場状態別の基準タイムでz-score正規化（フォールバック付き）
  - 後3F指数: コース・距離別の基準後3Fタイムでz-score正規化（フォールバック付き）
  - 騎手指数: 競馬場別に集計（データ不足時は全場合算にフォールバック）
  - 今走と同じコース・距離の過去走には重み1.5を適用（距離適性）

v3 変更点（CHIHOU_COMPOSITE_VERSION=3）:
  - place_ev_index 新設: 複勝確率（Harville）× 推定複勝オッズ（log-log回帰）
    EV=1.0（元本回収）→ index=50, EV>1.0（期待値プラス）→ index>50
  - 不人気の穴馬を複勝で狙う戦略に対応（COMPOSITE_WEIGHTS に place_ev: 0.25 追加）
  - 単勝オッズ不明時は place_ev を除いた4指数で正規化合成
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func, select, text
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

CHIHOU_COMPOSITE_VERSION = 4

# ばんえい競馬のコースコード
BANEI_COURSE_CODE = "83"

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
DISTANCE_MATCH_BONUS = 1.5  # 今走と同コース・距離の過去走への重み倍率

# 基準タイムに必要な最低サンプル数
PAR_TIME_MIN_COUNT = 10

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

# 総合指数の重み（v4: min-odds=15.0穴馬定義でNelder-Mead最適化 Cycle#13採用）
# v3 → v4: test upside_win_roi +1.3%（20260101-20260415, 過学習なし）
COMPOSITE_WEIGHTS = {
    "speed":    0.2954,
    "last3f":   0.2033,
    "jockey":   0.1481,
    "rotation": 0.0999,
    "place_ev": 0.2533,  # 複勝期待値指数（v3で新設）
}

# Softmax 温度パラメータ
SOFTMAX_TEMPERATURE = 10.0

# place_ev_index: 単勝オッズから複勝オッズを推定するlog-log回帰パラメータ
# 地方競馬統計より導出: place_odds ≈ PLACE_ODDS_COEF × win_odds^PLACE_ODDS_EXP
# (n=11,070, 3着内入着馬サンプル)
PLACE_ODDS_COEF = 0.8015
PLACE_ODDS_EXP  = 0.4562

# place_ev_index の中立EV（1.0=元本回収を指数50に対応）
EV_NEUTRAL = 1.0
EV_SCALE   = 20.0  # log(EV) × 20 + 50 → 指数化スケール

# 基準タイムの型エイリアス: (course, distance, condition) → (par_time, par_std)
ParTimeKey = tuple[str, int, str]
ParTimeValue = tuple[float, float]


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


def _estimate_place_odds(win_odds: float) -> float:
    """単勝オッズから複勝オッズを推定する（地方競馬統計より導出）。

    log-log回帰: place_odds ≈ PLACE_ODDS_COEF × win_odds^PLACE_ODDS_EXP
    (n=11,070, 3着内入着馬サンプル)

    Args:
        win_odds: 単勝オッズ（1.0以上）

    Returns:
        推定複勝オッズ
    """
    if win_odds <= 0.0:
        return 1.0
    return PLACE_ODDS_COEF * (win_odds ** PLACE_ODDS_EXP)


def _place_ev_to_index(place_probability: float, win_odds: float) -> float:
    """複勝期待値をEV指数（0-100）に変換する。

    EV = place_probability × estimated_place_odds
    EV=1.0（元本回収）→ index=50
    EV>1.0（期待値プラス）→ index>50
    EV<1.0（期待値マイナス）→ index<50

    Args:
        place_probability: 複勝確率（0.0〜1.0, Harvilleモデル出力）
        win_odds: 単勝オッズ

    Returns:
        place_ev_index（0〜100）
    """
    est_place_odds = _estimate_place_odds(win_odds)
    ev = place_probability * est_place_odds
    if ev <= 0.0:
        return INDEX_MIN
    index = math.log(ev) * EV_SCALE + 50.0
    return _clip(index)


def _weighted_avg(scores: list[float], weights: list[float]) -> float:
    """重み付き加重平均を計算する。

    Args:
        scores: スコアのリスト
        weights: 各スコアに対応する重みのリスト（同じ長さであること）

    Returns:
        加重平均値。リストが空の場合は INDEX_NEUTRAL を返す。
    """
    if not scores:
        return INDEX_NEUTRAL
    total_w = sum(weights)
    if total_w < 1e-9:
        return INDEX_NEUTRAL
    return sum(s * w for s, w in zip(scores, weights)) / total_w


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

    v2 の改善:
      - ばんえい競馬（course='83'）の除外
      - コース・距離・馬場状態別の基準タイム（par_time）をキャッシュして利用
      - 競馬場別騎手指数
      - 距離適性ボーナス重み
    """

    def __init__(self, db: AsyncSession) -> None:
        """初期化。

        Args:
            db: SQLAlchemy 非同期セッション
        """
        self.db = db
        # コース・距離・馬場状態別基準タイム（初回アクセス時にDBから取得）
        # key: (course, distance, condition), value: (par_time, par_std)
        self._par_times: dict[ParTimeKey, ParTimeValue] | None = None
        # コース・距離別基準後3Fタイム
        # key: (course, distance), value: (par_l3f, par_l3f_std)
        self._par_l3f: dict[tuple[str, int], tuple[float, float]] | None = None

    # ===================================================================
    # 公開 API
    # ===================================================================

    async def calculate_and_save(
        self,
        race_id: int,
        odds_map: dict[int, float] | None = None,
    ) -> dict[str, Any]:
        """指定レースの全馬指数を算出して DB に upsert する。

        ばんえい競馬（course='83'）は処理をスキップする。

        Args:
            race_id: chihou.races.id
            odds_map: horse_id → 単勝オッズ のマップ（リアルタイム用）。
                      None の場合は race_results テーブルから win_odds を取得する。

        Returns:
            {"saved": N, "skipped": M, "errors": K}
        """
        # レース情報取得
        race_row = await self.db.execute(select(ChihouRace).where(ChihouRace.id == race_id))
        race = race_row.scalar_one_or_none()
        if not race:
            logger.warning("chihou race not found: race_id=%d", race_id)
            return {"saved": 0, "skipped": 0, "errors": 1}

        # ばんえい競馬は除外
        if race.course == BANEI_COURSE_CODE:
            logger.debug("skip banei race: race_id=%d", race_id)
            return {"saved": 0, "skipped": 1, "errors": 0}

        # エントリ取得
        entry_rows = await self.db.execute(
            select(ChihouRaceEntry).where(ChihouRaceEntry.race_id == race_id)
        )
        entries: list[ChihouRaceEntry] = list(entry_rows.scalars().all())
        if not entries:
            logger.info("no entries for race_id=%d", race_id)
            return {"saved": 0, "skipped": 0, "errors": 0}

        race_date = _date_to_str(race.date)

        # 基準タイムを初回だけDBから取得してキャッシュ
        await self._ensure_par_times()
        await self._ensure_par_l3f()

        # 各指数をバッチ算出
        speed_map    = await self._speed_batch(race_id, race, entries)
        last3f_map   = await self._last3f_batch(race_id, race, entries)
        jockey_map   = await self._jockey_batch(race_date, race.course, entries)
        rotation_map = await self._rotation_batch(race_date, entries)

        # 単勝オッズを取得（odds_map が渡されない場合は race_results から取得）
        if odds_map is None:
            odds_map = await self._fetch_win_odds(race_id, entries)

        # 総合指数の仮算出（place_ev_index の入力となる place_probability を得るため）
        # Step 1: place_ev なしで一時的な composite を計算し Softmax → place_prob を得る
        base_weight_sum = (
            COMPOSITE_WEIGHTS["speed"]
            + COMPOSITE_WEIGHTS["last3f"]
            + COMPOSITE_WEIGHTS["jockey"]
            + COMPOSITE_WEIGHTS["rotation"]
        )
        pre_composite: list[tuple[int, float]] = []
        for entry in entries:
            hid = entry.horse_id
            s = speed_map.get(hid, INDEX_NEUTRAL)
            last3f = last3f_map.get(hid, INDEX_NEUTRAL)
            j = jockey_map.get(hid, INDEX_NEUTRAL)
            r = rotation_map.get(hid, INDEX_NEUTRAL)
            # base_weight_sum で正規化して 0-100 スケールを保つ
            comp_base = (
                COMPOSITE_WEIGHTS["speed"]    * s
                + COMPOSITE_WEIGHTS["last3f"]   * last3f
                + COMPOSITE_WEIGHTS["jockey"]   * j
                + COMPOSITE_WEIGHTS["rotation"] * r
            ) / base_weight_sum
            pre_composite.append((hid, _clip(comp_base)))

        pre_win_probs   = _softmax([v for _, v in pre_composite], SOFTMAX_TEMPERATURE)
        pre_place_probs = _harville_place_probs(pre_win_probs)

        # Step 2: place_ev_index を算出（Harvilleの複勝確率 × 推定複勝オッズ）
        place_ev_map: dict[int, float] = {}
        for idx, entry in enumerate(entries):
            hid = entry.horse_id
            win_odds = odds_map.get(hid)
            if win_odds is not None and win_odds > 0.0:
                place_ev_map[hid] = _place_ev_to_index(
                    pre_place_probs[idx], win_odds
                )
            # win_odds が不明な場合は place_ev_index = None（DB に NULL を格納）

        # Step 3: place_ev_index を含む最終 composite_index を算出
        composite_inputs: list[tuple[int, float]] = []
        for entry in entries:
            hid = entry.horse_id
            s = speed_map.get(hid, INDEX_NEUTRAL)
            last3f = last3f_map.get(hid, INDEX_NEUTRAL)
            j = jockey_map.get(hid, INDEX_NEUTRAL)
            r = rotation_map.get(hid, INDEX_NEUTRAL)
            pe = place_ev_map.get(hid)

            if pe is not None:
                # place_ev_index が算出できた場合は全5指数で合成
                comp = (
                    COMPOSITE_WEIGHTS["speed"]    * s
                    + COMPOSITE_WEIGHTS["last3f"]   * last3f
                    + COMPOSITE_WEIGHTS["jockey"]   * j
                    + COMPOSITE_WEIGHTS["rotation"] * r
                    + COMPOSITE_WEIGHTS["place_ev"] * pe
                )
            else:
                # オッズ不明の場合は place_ev を除いた4指数で合成（正規化）
                comp = (
                    COMPOSITE_WEIGHTS["speed"]    * s
                    + COMPOSITE_WEIGHTS["last3f"]   * last3f
                    + COMPOSITE_WEIGHTS["jockey"]   * j
                    + COMPOSITE_WEIGHTS["rotation"] * r
                ) / base_weight_sum
            composite_inputs.append((hid, _clip(comp)))

        # Softmax で単勝確率を推定
        comp_values = [v for _, v in composite_inputs]
        win_probs   = _softmax(comp_values, SOFTMAX_TEMPERATURE)
        # 複勝確率: Harville モデルで上位3着以内の確率を算出
        place_probs = _harville_place_probs(win_probs)

        # upsert
        values = []
        for i, (hid, comp) in enumerate(composite_inputs):
            values.append({
                "race_id":           race_id,
                "horse_id":          hid,
                "version":           CHIHOU_COMPOSITE_VERSION,
                "speed_index":       speed_map.get(hid, INDEX_NEUTRAL),
                "last3f_index":      last3f_map.get(hid, INDEX_NEUTRAL),
                "jockey_index":      jockey_map.get(hid, INDEX_NEUTRAL),
                "rotation_index":    rotation_map.get(hid, INDEX_NEUTRAL),
                "composite_index":   comp,
                "win_probability":   win_probs[i],
                "place_probability": min(1.0, place_probs[i]),
                "place_ev_index":    place_ev_map.get(hid),
                "calculated_at":     datetime.utcnow(),
            })

        if not values:
            return {"saved": 0, "skipped": 0, "errors": 0}

        stmt = (
            pg_insert(ChihouCalculatedIndex)
            .values(values)
            .on_conflict_do_update(
                constraint="uq_chihou_calc_idx_race_horse_ver",
                set_={
                    "speed_index":       pg_insert(ChihouCalculatedIndex).excluded.speed_index,
                    "last3f_index":      pg_insert(ChihouCalculatedIndex).excluded.last3f_index,
                    "jockey_index":      pg_insert(ChihouCalculatedIndex).excluded.jockey_index,
                    "rotation_index":    pg_insert(ChihouCalculatedIndex).excluded.rotation_index,
                    "composite_index":   pg_insert(ChihouCalculatedIndex).excluded.composite_index,
                    "win_probability":   pg_insert(ChihouCalculatedIndex).excluded.win_probability,
                    "place_probability": pg_insert(ChihouCalculatedIndex).excluded.place_probability,
                    "place_ev_index":    pg_insert(ChihouCalculatedIndex).excluded.place_ev_index,
                    "calculated_at":     pg_insert(ChihouCalculatedIndex).excluded.calculated_at,
                },
            )
        )
        await self.db.execute(stmt)
        logger.info("chihou indices saved: race_id=%d, horses=%d", race_id, len(values))
        return {"saved": len(values), "skipped": 0, "errors": 0}

    # ===================================================================
    # 基準タイムキャッシュ
    # ===================================================================

    async def _ensure_par_times(self) -> None:
        """コース・距離・馬場状態別基準タイムを初回だけDBから取得してキャッシュする。

        基準タイムは1着馬のfinish_timeの平均・標準偏差。
        PAR_TIME_MIN_COUNT 件以上のサンプルがある組み合わせのみ採用。
        ばんえい競馬（course='83'）は除外。
        """
        if self._par_times is not None:
            return

        q = text("""
            SELECT r.course, r.distance, r.condition,
                   AVG(rr.finish_time)      AS par_time,
                   STDDEV_POP(rr.finish_time) AS par_std,
                   COUNT(*)                 AS cnt
            FROM chihou.race_results rr
            JOIN chihou.races r ON r.id = rr.race_id
            WHERE r.course != :banei_course
              AND rr.finish_position = 1
              AND rr.abnormality_code = 0
              AND rr.finish_time IS NOT NULL
            GROUP BY r.course, r.distance, r.condition
            HAVING COUNT(*) >= :min_count
        """)

        rows = await self.db.execute(
            q,
            {"banei_course": BANEI_COURSE_CODE, "min_count": PAR_TIME_MIN_COUNT},
        )

        self._par_times = {}
        for row in rows.mappings():
            key: ParTimeKey = (
                str(row["course"]),
                int(row["distance"]),
                str(row["condition"]) if row["condition"] is not None else "",
            )
            par_time = float(row["par_time"])
            par_std  = float(row["par_std"]) if row["par_std"] is not None else 0.0
            if par_std >= 0.1:
                self._par_times[key] = (par_time, par_std)

        logger.info("par_times loaded: %d entries", len(self._par_times))

    async def _ensure_par_l3f(self) -> None:
        """コース・距離別基準後3Fタイムを初回だけDBから取得してキャッシュする。

        基準後3Fタイムは全着順（正常完走）のlast_3fの平均・標準偏差。
        PAR_TIME_MIN_COUNT 件以上のサンプルがある組み合わせのみ採用。
        ばんえい競馬（course='83'）は除外。
        """
        if self._par_l3f is not None:
            return

        q = text("""
            SELECT r.course, r.distance,
                   AVG(rr.last_3f)      AS par_l3f,
                   STDDEV_POP(rr.last_3f) AS par_l3f_std,
                   COUNT(*)             AS cnt
            FROM chihou.race_results rr
            JOIN chihou.races r ON r.id = rr.race_id
            WHERE r.course != :banei_course
              AND rr.abnormality_code = 0
              AND rr.last_3f IS NOT NULL
            GROUP BY r.course, r.distance
            HAVING COUNT(*) >= :min_count
        """)

        rows = await self.db.execute(
            q,
            {"banei_course": BANEI_COURSE_CODE, "min_count": PAR_TIME_MIN_COUNT},
        )

        self._par_l3f = {}
        for row in rows.mappings():
            key: tuple[str, int] = (str(row["course"]), int(row["distance"]))
            par_l3f     = float(row["par_l3f"])
            par_l3f_std = float(row["par_l3f_std"]) if row["par_l3f_std"] is not None else 0.0
            if par_l3f_std >= 0.05:
                self._par_l3f[key] = (par_l3f, par_l3f_std)

        logger.info("par_l3f loaded: %d entries", len(self._par_l3f))

    # ===================================================================
    # 単勝オッズ取得
    # ===================================================================

    async def _fetch_win_odds(
        self,
        race_id: int,
        entries: list[ChihouRaceEntry],
    ) -> dict[int, float]:
        """race_results テーブルから単勝オッズを取得する（バックフィル用）。

        Args:
            race_id: chihou.races.id
            entries: レースエントリのリスト

        Returns:
            horse_id → win_odds のマップ。オッズがない馬はキーに含まれない。
        """
        horse_ids = [e.horse_id for e in entries]
        if not horse_ids:
            return {}

        q = text("""
            SELECT horse_id, win_odds
            FROM chihou.race_results
            WHERE race_id = :race_id
              AND horse_id = ANY(:horse_ids)
              AND win_odds IS NOT NULL
              AND win_odds > 0
        """)
        rows = await self.db.execute(
            q, {"race_id": race_id, "horse_ids": horse_ids}
        )
        return {int(r["horse_id"]): float(r["win_odds"]) for r in rows.mappings()}

    # ===================================================================
    # スピード指数
    # ===================================================================

    async def _speed_batch(
        self,
        race_id: int,
        race: ChihouRace,
        entries: list[ChihouRaceEntry],
    ) -> dict[int, float]:
        """全エントリのスピード指数を一括算出する（v2）。

        アルゴリズム:
          1. 各馬の過去 SPEED_LOOKBACK 戦を取得（course/distance/condition 付き）
          2. 各過去レースの (course, distance, condition) に対応する基準タイム・stdでz-score計算
             z = (par_time - horse_time) / par_std（タイムが短いほど高スコア）
          3. 基準タイムがない場合はフィールド内z-scoreにフォールバック
          4. 斤量補正: (負担重量 - BASE_WEIGHT) × WEIGHT_CORR_PER_KG 秒を finish_time から加算
          5. 今走と同じ course・distance の過去走には DISTANCE_MATCH_BONUS 倍の重みを適用
          6. 減衰加重平均でスコア化
        """
        race_date = _date_to_str(race.date)
        horse_ids = [e.horse_id for e in entries]
        current_course = str(race.course)
        current_distance = int(race.distance)

        # 各馬の過去結果を一括取得（course/distance/condition 付き）
        past_results = await self._get_past_results_batch(
            horse_ids, race_date, exclude_race_id=race_id, limit_per_horse=SPEED_LOOKBACK
        )
        if not past_results:
            return {}

        # フォールバック用: フィールド内時刻統計を一括取得
        involved_race_ids = list({r["race_id"] for r in past_results})
        field_stats = await self._get_field_time_stats(involved_race_ids)

        # 馬ごとに (score, weight) を集計
        horse_score_weights: dict[int, list[tuple[float, float]]] = defaultdict(list)

        for i_row, row in enumerate(past_results):
            hid = row["horse_id"]
            rid = row["race_id"]
            ft  = row["finish_time"]
            wc  = row["weight_carried"]
            ab  = row["abnormality_code"]
            row_course    = str(row["course"]) if row["course"] is not None else ""
            row_distance  = int(row["distance"]) if row["distance"] is not None else 0
            row_condition = str(row["condition"]) if row["condition"] is not None else ""

            if ab and ab > 0:
                continue
            if ft is None:
                continue

            # 斤量補正（重い = タイムを短く補正）
            ft_float = float(ft)
            if wc is not None:
                ft_float += (float(wc) - BASE_WEIGHT) * WEIGHT_CORR_PER_KG

            # 同じ馬の中で何番目か（新しい順）を求めて減衰重みを計算
            # past_results は horse_id, date DESC 順なので、同馬内のインデックスを追跡
            # 後の処理で rn（行番号）を使うが、ここでは簡略化のため後からまとめる
            # → 一旦 horse_score_weights[hid] に追記し、後で重み付けする

            par_key: ParTimeKey = (row_course, row_distance, row_condition)

            if self._par_times and par_key in self._par_times:
                par_time, par_std = self._par_times[par_key]
                z = (par_time - ft_float) / par_std
                score = _zscore_to_index(z)
            else:
                # フォールバック: フィールド内z-score
                stats = field_stats.get(rid)
                if not stats or stats["cnt"] < SPEED_MIN_FIELD:
                    continue
                mean_t = stats["mean"]
                std_t  = stats["std"]
                if mean_t is None or std_t is None or std_t < 0.1:
                    continue
                z = (mean_t - ft_float) / std_t
                score = _zscore_to_index(z)

            # 距離適性ボーナス（今走と同じコース・距離）
            distance_bonus = (
                DISTANCE_MATCH_BONUS
                if row_course == current_course and row_distance == current_distance
                else 1.0
            )
            horse_score_weights[hid].append((score, distance_bonus))

        # 減衰重みを適用して加重平均
        result: dict[int, float] = {}
        for hid, sw_list in horse_score_weights.items():
            scores  = [s for s, _ in sw_list]
            # 減衰重み × 距離ボーナス
            weights = [
                SPEED_WEIGHT_DECAY ** i * bonus
                for i, (_, bonus) in enumerate(sw_list)
            ]
            result[hid] = _weighted_avg(scores, weights)

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
        """全エントリの後3ハロン指数を一括算出する（v2）。

        アルゴリズム:
          1. 各馬の過去 L3F_LOOKBACK 戦を取得（course/distance 付き）
          2. 各過去レースの (course, distance) に対応する基準後3Fタイム・stdでz-score計算
             z = (par_l3f - horse_l3f) / par_l3f_std（末脚が速いほど高スコア）
          3. 基準タイムがない場合はフィールド内z-scoreにフォールバック
          4. 今走と同じ course・distance の過去走には DISTANCE_MATCH_BONUS 倍の重みを適用
        """
        race_date = _date_to_str(race.date)
        horse_ids = [e.horse_id for e in entries]
        current_course   = str(race.course)
        current_distance = int(race.distance)

        past_results = await self._get_past_results_batch(
            horse_ids, race_date, exclude_race_id=race_id, limit_per_horse=L3F_LOOKBACK
        )
        if not past_results:
            return {}

        involved_race_ids = list({r["race_id"] for r in past_results})
        field_stats = await self._get_field_l3f_stats(involved_race_ids)

        horse_score_weights: dict[int, list[tuple[float, float]]] = defaultdict(list)

        for row in past_results:
            hid = row["horse_id"]
            rid = row["race_id"]
            l3f = row["last_3f"]
            ab  = row["abnormality_code"]
            row_course   = str(row["course"]) if row["course"] is not None else ""
            row_distance = int(row["distance"]) if row["distance"] is not None else 0

            if ab and ab > 0:
                continue
            if l3f is None:
                continue

            l3f_float = float(l3f)
            l3f_key: tuple[str, int] = (row_course, row_distance)

            if self._par_l3f and l3f_key in self._par_l3f:
                par_l3f, par_l3f_std = self._par_l3f[l3f_key]
                z = (par_l3f - l3f_float) / par_l3f_std
                score = _zscore_to_index(z)
            else:
                # フォールバック: フィールド内z-score
                stats = field_stats.get(rid)
                if not stats or stats["cnt"] < L3F_MIN_FIELD:
                    continue
                mean_l = stats["mean"]
                std_l  = stats["std"]
                if mean_l is None or std_l is None or std_l < 0.05:
                    continue
                z = (mean_l - l3f_float) / std_l
                score = _zscore_to_index(z)

            distance_bonus = (
                DISTANCE_MATCH_BONUS
                if row_course == current_course and row_distance == current_distance
                else 1.0
            )
            horse_score_weights[hid].append((score, distance_bonus))

        result: dict[int, float] = {}
        for hid, sw_list in horse_score_weights.items():
            scores  = [s for s, _ in sw_list]
            weights = [
                L3F_WEIGHT_DECAY ** i * bonus
                for i, (_, bonus) in enumerate(sw_list)
            ]
            result[hid] = _weighted_avg(scores, weights)

        return result

    # ===================================================================
    # 騎手指数
    # ===================================================================

    async def _jockey_batch(
        self,
        race_date: str,
        race_course: str,
        entries: list[ChihouRaceEntry],
    ) -> dict[int, float]:
        """全エントリの騎手指数を一括算出する（v2: 競馬場別）。

        過去 JOCKEY_LOOKBACK_DAYS 日間の、同じ競馬場での勝率・連対率から算出する。
        同じ競馬場のデータが JOCKEY_MIN_RIDES 未満の場合は全場合算にフォールバックする。

        Args:
            race_date: 今走の日付（YYYYMMDD）
            race_course: 今走の競馬場コード
            entries: 出走馬エントリリスト
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

        # 競馬場別集計
        course_rows = await self.db.execute(
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
                    ChihouRace.course == race_course,
                    ChihouRaceResult.abnormality_code.in_([0, None]),
                    ChihouRaceResult.finish_position.is_not(None),
                )
            )
            .group_by(ChihouRaceResult.jockey_id)
        )

        # 全場合算集計（フォールバック用）
        all_rows = await self.db.execute(
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

        def _calc_jockey_score(rides: int, wins: int, places: int) -> float | None:
            """騎手スコアを計算する。データ不足の場合 None を返す。"""
            if rides < JOCKEY_MIN_RIDES:
                return None
            win_rate   = wins   / rides
            place_rate = places / rides
            win_score   = _clip(win_rate   * 200.0 + 30.0)
            place_score = _clip(place_rate * 100.0 + 20.0)
            return _clip(win_score * 0.6 + place_score * 0.4)

        # 競馬場別スコア
        course_scores: dict[int, float] = {}
        for row in course_rows.mappings():
            jid    = row["jockey_id"]
            rides  = int(row["rides"] or 0)
            wins   = int(row["wins"] or 0)
            places = int(row["places"] or 0)
            score  = _calc_jockey_score(rides, wins, places)
            if score is not None:
                course_scores[jid] = score

        # 全場合算スコア（フォールバック）
        all_scores: dict[int, float] = {}
        for row in all_rows.mappings():
            jid    = row["jockey_id"]
            rides  = int(row["rides"] or 0)
            wins   = int(row["wins"] or 0)
            places = int(row["places"] or 0)
            score  = _calc_jockey_score(rides, wins, places)
            if score is not None:
                all_scores[jid] = score

        result: dict[int, float] = {}
        for jid, hids in jockey_to_horses.items():
            # 競馬場別 → 全場合算 → デフォルト の優先順
            score = course_scores.get(jid) or all_scores.get(jid) or INDEX_NEUTRAL
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

        v2 では course, distance, condition も返す。

        Args:
            horse_ids: 対象馬IDリスト
            before_date: この日付より前のレース（YYYYMMDD）
            exclude_race_id: 除外するレースID（通常は当該レース）
            limit_per_horse: 馬ごとの最大取得件数

        Returns:
            [{"horse_id", "race_id", "finish_time", "last_3f",
              "weight_carried", "abnormality_code",
              "course", "distance", "condition"}, ...]
        """
        if not horse_ids:
            return []

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
                    r.course,
                    r.distance,
                    r.condition,
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
                   weight_carried, abnormality_code,
                   course, distance, condition
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
