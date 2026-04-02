"""血統指数算出Agent（データ駆動型）

父（sire）と母父（sire_of_dam）の実績統計から血統適性スコアを算出する。

算出ロジック:
  1. pedigrees テーブルから父名・母父名を取得
  2. race_results × pedigrees × races から種牡馬別の条件別実績統計を集計（初回のみ）
     - 芝/ダート別勝率
     - 距離カテゴリ別勝率 (sprint/mile/middle/long)
     - 競馬場別勝率 (JRA10場)
     - 洋芝/野芝別勝率 (芝レースのみ)
     - 斤量カテゴリ別勝率 (light/normal/heavy)
  3. 各条件での適性スコアを z-score → 0-100 スケールで算出
     - z = (sire_win_rate - 母集団平均) / 母集団標準偏差
     - score = 50 + z × (50 / 3.0)  ← ±3σ が 0/100 に対応
  4. 信頼度ブレンド: reliability = min(1.0, cnt / RELIABLE_SAMPLES)
     final = reliability × score + (1 - reliability) × 50.0
  5. 各因子の重み付き合成 → 父(0.65) + 母父(0.35) 加重平均

フォールバック:
  - pedigrees 未登録 → SPEED_INDEX_MEAN（50.0）
  - 種牡馬の実績サンプルなし → ニュートラル（50.0）
  - pedigrees テーブルが空 → 全馬ニュートラル（50.0）
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db.models import Pedigree, Race, RaceEntry
from ..utils.constants import SPEED_INDEX_MEAN
from .base import IndexCalculator

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 定数
# ─────────────────────────────────────────────

DIST_SPRINT_MAX = 1400  # スプリント: ≤1400m
DIST_MILE_MAX = 1800  # マイル: 1401-1800m
DIST_MIDDLE_MAX = 2400  # 中距離: 1801-2400m
# 長距離: >2400m

# JRA競馬場コード
JRA_COURSES = ("01", "02", "03", "04", "05", "06", "07", "08", "09", "10")

# 斤量カテゴリ境界 (kg)
WEIGHT_LIGHT_MAX = 55.0  # ≤55.0 = light
WEIGHT_HEAVY_MIN = 57.5  # ≥57.5 = heavy

# 父/母父の重み
SIRE_WEIGHT = 0.65
DAM_SIRE_WEIGHT = 0.35

# 各因子の重み (合計 1.0)
FACTOR_WEIGHTS: dict[str, float] = {
    "surface": 0.35,
    "dist_cat": 0.25,
    "course": 0.20,
    "grass": 0.10,
    "weight": 0.10,
}

# z-score スケーリング: ±3σ = 0/100 に対応
ZSCORE_SCALE = 3.0

# 信頼度計算の基準サンプル数（この件数でブレンド係数=1.0）
RELIABLE_SAMPLES = 20

NEUTRAL = SPEED_INDEX_MEAN  # 50.0

INDEX_MIN = 0.0
INDEX_MAX = 100.0


# ─────────────────────────────────────────────
# ヘルパー関数
# ─────────────────────────────────────────────


def _dist_category(distance: int | None) -> str:
    """距離（メートル）を距離カテゴリ名に変換する。"""
    if not distance:
        return "mile"
    if distance <= DIST_SPRINT_MAX:
        return "sprint"
    if distance <= DIST_MILE_MAX:
        return "mile"
    if distance <= DIST_MIDDLE_MAX:
        return "middle"
    return "long"


def _surface_key(surface: str | None) -> str:
    """DB の surface 値を "turf"/"dirt" に正規化する。"""
    if surface and surface.startswith("芝"):
        return "turf"
    return "dirt"


def _weight_cat(weight_carried: float | None) -> str:
    """斤量（kg）をカテゴリ名に変換する。"""
    if weight_carried is None:
        return "normal"
    w = float(weight_carried)
    if w <= WEIGHT_LIGHT_MAX:
        return "light"
    if w >= WEIGHT_HEAVY_MIN:
        return "heavy"
    return "normal"


# ─────────────────────────────────────────────
# 種牡馬実績統計キャッシュ
# ─────────────────────────────────────────────


@dataclass
class _CondStats:
    """ある条件での実績統計。"""

    cnt: int
    win_rate: float
    place_rate: float


class SireStatsCache:
    """種牡馬別条件統計のキャッシュ。

    DBから一括集計し、以下の構造で保持する:
      stats[sire_name][factor][factor_value] = _CondStats
      pop[factor][factor_value] = {"mean": float, "std": float}

    最初の ensure_loaded() 呼び出し時に集計を実行する。
    pedigrees が空の場合は stats も空のまま（全馬ニュートラル）。
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self._loaded = False
        # {sire_name → {factor → {value → _CondStats}}}
        self.stats: dict[str, dict[str, dict[str, _CondStats]]] = {}
        # {factor → {value → {"mean": float, "std": float}}}
        self.pop: dict[str, dict[str, dict[str, float]]] = {}
        # {course_code → grass_type}
        self.course_grass: dict[str, str] = {}

    def ensure_loaded(self) -> None:
        """未ロードの場合のみ集計を実行する。"""
        if not self._loaded:
            self._load()
            self._loaded = True

    # ──────────────────────────────────────────
    # ロード処理
    # ──────────────────────────────────────────

    def _load(self) -> None:
        """種牡馬別条件統計を DB から一括集計する。"""
        # pedigrees が空ならスキップ
        try:
            cnt = self.db.execute(text("SELECT COUNT(*) FROM keiba.pedigrees")).scalar()
        except Exception:
            cnt = 0
        if not cnt:
            logger.info("SireStatsCache: pedigrees テーブルが空 → 全馬ニュートラル")
            return

        # コース特徴（洋芝/野芝）をロード
        try:
            rows = self.db.execute(
                text("SELECT course_code, grass_type FROM keiba.racecourse_features")
            ).fetchall()
            self.course_grass = {r[0]: r[1] for r in rows}
        except Exception as e:
            logger.warning(f"racecourse_features 取得失敗: {e}")

        jra_in = "'" + "','".join(JRA_COURSES) + "'"

        # 父（sire）の統計
        self._load_factor(
            "surface",
            f"""
            SELECT p.sire,
                   CASE WHEN ra.surface LIKE '芝%%' THEN 'turf' ELSE 'dirt' END surface_key,
                   COUNT(*) cnt,
                   AVG(CASE WHEN rr.finish_position=1 THEN 1.0 ELSE 0.0 END) win_rate,
                   AVG(CASE WHEN rr.finish_position<=3 THEN 1.0 ELSE 0.0 END) place_rate
            FROM keiba.race_results rr
            JOIN keiba.races ra ON ra.id = rr.race_id
            JOIN keiba.pedigrees p ON p.horse_id = rr.horse_id
            WHERE rr.finish_position IS NOT NULL AND rr.abnormality_code = 0
              AND p.sire IS NOT NULL AND ra.course IN ({jra_in})
            GROUP BY p.sire, surface_key
        """,
        )

        self._load_factor(
            "dist_cat",
            f"""
            SELECT p.sire,
                   CASE WHEN ra.distance <= 1400 THEN 'sprint'
                        WHEN ra.distance <= 1800 THEN 'mile'
                        WHEN ra.distance <= 2400 THEN 'middle'
                        ELSE 'long' END dist_cat,
                   COUNT(*) cnt,
                   AVG(CASE WHEN rr.finish_position=1 THEN 1.0 ELSE 0.0 END) win_rate,
                   AVG(CASE WHEN rr.finish_position<=3 THEN 1.0 ELSE 0.0 END) place_rate
            FROM keiba.race_results rr
            JOIN keiba.races ra ON ra.id = rr.race_id
            JOIN keiba.pedigrees p ON p.horse_id = rr.horse_id
            WHERE rr.finish_position IS NOT NULL AND rr.abnormality_code = 0
              AND p.sire IS NOT NULL AND ra.course IN ({jra_in})
            GROUP BY p.sire, dist_cat
        """,
        )

        self._load_factor(
            "course",
            f"""
            SELECT p.sire, ra.course,
                   COUNT(*) cnt,
                   AVG(CASE WHEN rr.finish_position=1 THEN 1.0 ELSE 0.0 END) win_rate,
                   AVG(CASE WHEN rr.finish_position<=3 THEN 1.0 ELSE 0.0 END) place_rate
            FROM keiba.race_results rr
            JOIN keiba.races ra ON ra.id = rr.race_id
            JOIN keiba.pedigrees p ON p.horse_id = rr.horse_id
            WHERE rr.finish_position IS NOT NULL AND rr.abnormality_code = 0
              AND p.sire IS NOT NULL AND ra.course IN ({jra_in})
            GROUP BY p.sire, ra.course
        """,
        )

        self._load_factor(
            "grass",
            f"""
            SELECT p.sire, rf.grass_type,
                   COUNT(*) cnt,
                   AVG(CASE WHEN rr.finish_position=1 THEN 1.0 ELSE 0.0 END) win_rate,
                   AVG(CASE WHEN rr.finish_position<=3 THEN 1.0 ELSE 0.0 END) place_rate
            FROM keiba.race_results rr
            JOIN keiba.races ra ON ra.id = rr.race_id
            JOIN keiba.racecourse_features rf ON rf.course_code = ra.course
            JOIN keiba.pedigrees p ON p.horse_id = rr.horse_id
            WHERE rr.finish_position IS NOT NULL AND rr.abnormality_code = 0
              AND p.sire IS NOT NULL AND ra.surface NOT LIKE 'ダ%%'
              AND ra.course IN ({jra_in})
            GROUP BY p.sire, rf.grass_type
        """,
        )

        self._load_factor(
            "weight",
            f"""
            SELECT p.sire,
                   CASE WHEN re.weight_carried <= 55.0 THEN 'light'
                        WHEN re.weight_carried >= 57.5 THEN 'heavy'
                        ELSE 'normal' END weight_cat,
                   COUNT(*) cnt,
                   AVG(CASE WHEN rr.finish_position=1 THEN 1.0 ELSE 0.0 END) win_rate,
                   AVG(CASE WHEN rr.finish_position<=3 THEN 1.0 ELSE 0.0 END) place_rate
            FROM keiba.race_results rr
            JOIN keiba.races ra ON ra.id = rr.race_id
            JOIN keiba.race_entries re ON re.race_id=rr.race_id AND re.horse_id=rr.horse_id
            JOIN keiba.pedigrees p ON p.horse_id = rr.horse_id
            WHERE rr.finish_position IS NOT NULL AND rr.abnormality_code = 0
              AND p.sire IS NOT NULL AND re.weight_carried IS NOT NULL
              AND ra.course IN ({jra_in})
            GROUP BY p.sire, weight_cat
        """,
        )

        # 母父（sire_of_dam）の統計（父と同じ因子を集計）
        self._load_factor(
            "surface",
            f"""
            SELECT p.sire_of_dam,
                   CASE WHEN ra.surface LIKE '芝%%' THEN 'turf' ELSE 'dirt' END surface_key,
                   COUNT(*) cnt,
                   AVG(CASE WHEN rr.finish_position=1 THEN 1.0 ELSE 0.0 END) win_rate,
                   AVG(CASE WHEN rr.finish_position<=3 THEN 1.0 ELSE 0.0 END) place_rate
            FROM keiba.race_results rr
            JOIN keiba.races ra ON ra.id = rr.race_id
            JOIN keiba.pedigrees p ON p.horse_id = rr.horse_id
            WHERE rr.finish_position IS NOT NULL AND rr.abnormality_code = 0
              AND p.sire_of_dam IS NOT NULL AND ra.course IN ({jra_in})
              AND p.sire_of_dam NOT IN (SELECT DISTINCT sire FROM keiba.pedigrees WHERE sire IS NOT NULL)
            GROUP BY p.sire_of_dam, surface_key
        """,
            merge=True,
        )  # merge=True: 既存エントリとマージ（片方にしかない馬）

        # 母集団統計（平均・標準偏差）を計算
        self._compute_pop_stats()

        logger.info(f"SireStatsCache: {len(self.stats):,}種牡馬分の統計を集計完了")

    def _load_factor(self, factor_name: str, sql: str, merge: bool = False) -> None:
        """SQL結果をキャッシュに格納する。

        Args:
            factor_name: 因子名 (surface/dist_cat/course/grass/weight)
            sql: 集計SQL (col0=名前, col1=factor値, col2=cnt, col3=win_rate, col4=place_rate)
            merge: True のとき既存エントリが存在する場合はスキップする
        """
        try:
            rows = self.db.execute(text(sql)).fetchall()
        except Exception as e:
            logger.warning(f"SireStatsCache._load_factor({factor_name}): {e}")
            return

        added = 0
        for row in rows:
            name = row[0]
            if not name:
                continue
            val = str(row[1])
            cnt = int(row[2]) if row[2] else 0
            win_rate = float(row[3]) if row[3] is not None else 0.0
            place_rate = float(row[4]) if row[4] is not None else 0.0

            if name not in self.stats:
                self.stats[name] = {}
            if factor_name not in self.stats[name]:
                self.stats[name][factor_name] = {}

            # merge=True のとき既存エントリをスキップ
            if merge and val in self.stats[name][factor_name]:
                continue

            self.stats[name][factor_name][val] = _CondStats(
                cnt=cnt, win_rate=win_rate, place_rate=place_rate
            )
            added += 1

        logger.debug(f"SireStatsCache._load_factor({factor_name}): {added}件追加")

    def _compute_pop_stats(self) -> None:
        """各因子・条件値の母集団統計（平均・標準偏差）を計算する。

        信頼性の低いサンプルを除外し、安定した基準値を算出する。
        """
        for factor in FACTOR_WEIGHTS:
            self.pop[factor] = {}
            by_value: dict[str, list[float]] = {}

            for sire_data in self.stats.values():
                for val, cs in sire_data.get(factor, {}).items():
                    if cs.cnt >= RELIABLE_SAMPLES:
                        by_value.setdefault(val, []).append(cs.win_rate)

            for val, rates in by_value.items():
                if len(rates) >= 3:
                    mu = statistics.mean(rates)
                    sigma = statistics.stdev(rates) if len(rates) > 1 else 0.01
                else:
                    mu, sigma = 0.08, 0.02  # JRA全体の平均的な勝率
                self.pop[factor][val] = {"mean": mu, "std": max(sigma, 0.001)}

    # ──────────────────────────────────────────
    # スコア取得
    # ──────────────────────────────────────────

    def aptitude_score(
        self,
        name: str | None,
        factor: str,
        value: str,
    ) -> float:
        """種牡馬の特定条件での適性スコア（0-100）を返す。

        Args:
            name: 種牡馬名
            factor: 因子名 (surface/dist_cat/course/grass/weight)
            value: 条件値 (例: "turf", "sprint", "05")

        Returns:
            適性スコア 0-100（データ不足時は NEUTRAL=50.0）
        """
        if not name or not self.stats:
            return NEUTRAL

        cs = self.stats.get(name, {}).get(factor, {}).get(value)
        pop = self.pop.get(factor, {}).get(value)

        if cs is None or pop is None:
            return NEUTRAL

        # z-score → 0-100
        z = (cs.win_rate - pop["mean"]) / (pop["std"] + 1e-9)
        raw = 50.0 + z * (50.0 / ZSCORE_SCALE)
        raw = max(INDEX_MIN, min(INDEX_MAX, raw))

        # 信頼度ブレンド
        reliability = min(1.0, cs.cnt / RELIABLE_SAMPLES)
        return reliability * raw + (1.0 - reliability) * NEUTRAL

    def course_grass_type(self, course_code: str | None) -> str | None:
        """コードから洋芝/野芝タイプを返す。"""
        if not course_code:
            return None
        return self.course_grass.get(course_code)


# ─────────────────────────────────────────────
# 血統指数算出 Agent
# ─────────────────────────────────────────────


class PedigreeIndexCalculator(IndexCalculator):
    """血統指数算出Agent（データ駆動型）。

    IndexCalculator を継承し、単一馬（calculate）と
    レース全馬バッチ（calculate_batch）の両インターフェースを提供する。

    初回 calculate_batch 呼び出し時に SireStatsCache を初期化する。
    """

    def __init__(self, db: Session) -> None:
        super().__init__(db)
        self._cache = SireStatsCache(db)

    # ──────────────────────────────────────────
    # 公開インターフェース
    # ──────────────────────────────────────────

    def calculate(self, race_id: int, horse_id: int) -> float:
        """単一馬の血統指数を算出する。

        Args:
            race_id: DB の races.id
            horse_id: DB の horses.id

        Returns:
            血統指数（0-100）。データ未登録時は SPEED_INDEX_MEAN。
        """
        self._cache.ensure_loaded()

        race = self.db.query(Race).filter(Race.id == race_id).first()
        if not race:
            return SPEED_INDEX_MEAN

        pedigree = self.db.query(Pedigree).filter(Pedigree.horse_id == horse_id).first()
        if pedigree is None:
            return SPEED_INDEX_MEAN

        entry = (
            self.db.query(RaceEntry)
            .filter(RaceEntry.race_id == race_id, RaceEntry.horse_id == horse_id)
            .first()
        )
        weight_carried = float(entry.weight_carried) if entry and entry.weight_carried else None

        return self._compute_score(pedigree, race, weight_carried)

    def calculate_batch(self, race_id: int) -> dict[int, float]:
        """レース全馬の血統指数を一括算出する。

        Args:
            race_id: DB の races.id

        Returns:
            {horse_id: pedigree_index} の dict。エントリなし時は空 dict。
        """
        self._cache.ensure_loaded()

        race = self.db.query(Race).filter(Race.id == race_id).first()
        if not race:
            return {}

        entries = self.db.query(RaceEntry).filter(RaceEntry.race_id == race_id).all()
        if not entries:
            return {}

        horse_ids = [e.horse_id for e in entries]
        pedigrees = self.db.query(Pedigree).filter(Pedigree.horse_id.in_(horse_ids)).all()
        ped_map: dict[int, Pedigree] = {p.horse_id: p for p in pedigrees}
        weight_map: dict[int, float | None] = {
            e.horse_id: (float(e.weight_carried) if e.weight_carried else None) for e in entries
        }

        result: dict[int, float] = {}
        for entry in entries:
            ped = ped_map.get(entry.horse_id)
            if ped is None:
                result[entry.horse_id] = SPEED_INDEX_MEAN
            else:
                result[entry.horse_id] = self._compute_score(
                    ped, race, weight_map.get(entry.horse_id)
                )

        return result

    # ──────────────────────────────────────────
    # 内部メソッド
    # ──────────────────────────────────────────

    def _compute_score(
        self,
        pedigree: Pedigree,
        race: Race,
        weight_carried: float | None,
    ) -> float:
        """血統 × レース条件のスコアを算出する。

        Args:
            pedigree: Pedigree ORM オブジェクト
            race: Race ORM オブジェクト
            weight_carried: 斤量 (kg)、不明の場合は None

        Returns:
            血統指数（0-100）
        """
        surface = _surface_key(race.surface)
        dist_cat = _dist_category(race.distance)
        course = race.course or ""
        grass = self._cache.course_grass_type(course) or ""
        weight = _weight_cat(weight_carried)

        sire_score = self._factor_score(pedigree.sire, surface, dist_cat, course, grass, weight)
        dam_sire_score = self._factor_score(
            pedigree.sire_of_dam, surface, dist_cat, course, grass, weight
        )

        # 芝レース以外は grass 因子を surface に振り替え（ダートに洋芝/野芝区別なし）
        if surface == "dirt":
            # grass factor を無効化し surface factor に集約
            grass_w = FACTOR_WEIGHTS["grass"]
            _adj = {"surface": FACTOR_WEIGHTS["surface"] + grass_w, "grass": 0.0}
        else:
            _adj = {}

        score = sire_score * SIRE_WEIGHT + dam_sire_score * DAM_SIRE_WEIGHT
        return round(max(INDEX_MIN, min(INDEX_MAX, score)), 1)

    def _factor_score(
        self,
        sire_name: str | None,
        surface: str,
        dist_cat: str,
        course: str,
        grass: str,
        weight: str,
    ) -> float:
        """1種牡馬の全因子加重合成スコアを算出する。

        Args:
            sire_name: 種牡馬名
            surface: "turf" | "dirt"
            dist_cat: "sprint" | "mile" | "middle" | "long"
            course: 競馬場コード e.g. "05"
            grass: 洋芝/野芝タイプ e.g. "洋芝" | "野芝+洋芝"
            weight: "light" | "normal" | "heavy"

        Returns:
            加重合成スコア 0-100
        """
        c = self._cache
        scores = {
            "surface": c.aptitude_score(sire_name, "surface", surface),
            "dist_cat": c.aptitude_score(sire_name, "dist_cat", dist_cat),
            "course": c.aptitude_score(sire_name, "course", course),
            "grass": c.aptitude_score(sire_name, "grass", grass) if grass else NEUTRAL,
            "weight": c.aptitude_score(sire_name, "weight", weight),
        }

        # ダートレースは grass 重みを surface に加算
        weights = dict(FACTOR_WEIGHTS)
        if surface == "dirt":
            weights["surface"] += weights["grass"]
            weights["grass"] = 0.0

        total = sum(scores[f] * weights[f] for f in scores)
        return max(INDEX_MIN, min(INDEX_MAX, total))
