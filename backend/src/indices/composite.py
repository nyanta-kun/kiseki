"""総合指数算出Agent

各単体指数（スピード・コース適性・枠順・ローテーション・騎手・展開・血統・調教・穴ぐさ）を
INDEX_WEIGHTS で重み付け合成し、総合指数を算出する。

未実装指数（パドック）はニュートラル値（50.0）で補完する。
算出結果は calculated_indices テーブルへ upsert する。

重み構成（constants.INDEX_WEIGHTS 準拠）:
  speed              0.1251 (SpeedIndexCalculator)
  last_3f            0.1168 (Last3FIndexCalculator)
  course_aptitude    0.3097 (CourseAptitudeCalculator)
  pace               0.0399 (PaceIndexCalculator)
  jockey_trainer     0.1213 (JockeyIndexCalculator)
  pedigree           0.0662 (PedigreeIndexCalculator)
  rotation           0.1124 (RotationIndexCalculator)
  training           0.0327 (TrainingIndexCalculator: タイムトレンド+上がり3F+体重)
  position_advantage 0.0259 (FrameBiasCalculator)
  anagusa            0.0000 (AnagusaIndexCalculator: 穴ぐさピック期待度)
  paddock            0.0000 (PaddockIndexCalculator: 発走前パドック状態)
  disadvantage_bonus 0.05  (未実装 → 0.0 加算なし)

合計重み: 1.00 (disadvantage_bonus は加算方式のため別途処理)
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime
from decimal import Decimal

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import CalculatedIndex, Race, RaceEntry
from ..db.session import AsyncSessionLocal
from ..utils.constants import INDEX_WEIGHTS, SPEED_INDEX_MEAN
from .anagusa import AnagusaIndexCalculator
from .career_phase import CareerPhaseIndexCalculator
from .course_aptitude import CourseAptitudeCalculator
from .distance_change import DistanceChangeIndexCalculator
from .frame_bias import FrameBiasCalculator
from .going_pedigree import GoingPedigreeIndexCalculator
from .jockey import JockeyIndexCalculator
from .jockey_trainer_combo import JockeyTrainerComboIndexCalculator
from .last3f import Last3FIndexCalculator
from .pace import PaceIndexCalculator
from .paddock import PaddockIndexCalculator
from .pedigree import PedigreeIndexCalculator
from .rebound import ReboundIndexCalculator
from .rivals_growth import RivalsGrowthIndexCalculator
from .rotation import RotationIndexCalculator
from .speed import SpeedIndexCalculator
from .training import TrainingIndexCalculator

logger = logging.getLogger(__name__)

# 算出バージョン（ロジック変更時にインクリメント）
# v2: コース適性改善（類似コースフォールバック+信頼度加重）
#     FrameBias/PaceIndex に開催バイアス・コース特性・頭数補正を追加
# v3: 血統指数改善（SKコードによる実績ベース統計計算）
# v4: 調教指数実装（タイムトレンド+上がり3F改善+体重コンディション）
# v5: 穴ぐさ指数追加（sekito.anagusa ピック実績ベース、speed -0.03/last_3f -0.02 から拠出）
# v6: パドック指数追加（sekito.netkeiba p_rank ベース、speed -0.03 から拠出）
# v7: Nelder-Mead重み最適化。コース適性 0.13→0.31、スピード 0.24→0.13。穴ぐさ・パドック=0
# v8: スピアマン相関比例重み。後3F(0.117→0.171)・血統(0.066→0.118)を増。
#     コース適性(0.310→0.167)・ローテ(0.112→0.113)を調整。テストROI=86.4%（v7比+10.3%）
# v9: 巻き返し指数追加（ReboundIndexCalculator）。disadvantage_bonus(0.05)を rebound として活用。
#     バックフィルデータ（2024-01〜）が揃い次第、重み最適化予定。
# v10: 既存バグ修正（重みは v9 から変更なし）
#   ① 騎手指数の上がり3Fスコアが常に50だったバグを修正:
#      _compute_last3f_score の mean_all が mean_jockey と同値（自己参照）だった
#      → 全騎手データのグローバル後3F平均と各騎手の平均を比較するよう変更
#   ② ローテーション指数のタイムボーナスが常に0だったバグを修正:
#      _estimate_speed_score_sync が常に None を返していた
#      → calculate_batch に speed_map 引数を追加し、CompositeIndexCalculator から渡す
# v11: 再帰的改善 Cycle#1 採用 (2026-04-11)
#   Nelder-Mead最適化 (目標: 穴馬単勝ROI, 訓練: 20190101-20231231, テスト: 20250101-20250630)
#   テスト期間: 穴馬ROI +7.9%, 全体ROI +2.7%, 3着内率 +0.2%
#   全ベース指数を均等に▼2-3%調整。交互作用項(20%バジェット)は upside_score として別途実装予定。
# v12: 上昇相手指数（rivals_growth）追加 (2026-04-11)
#   過去レースで負かした相手馬が後に上位クラスで活躍していれば高スコア。
#   disadvantage_bonus から 0.020 を拠出。初期値設定。次回重み最適化で調整予定。
# v13: career_phase / distance_change / jockey_trainer_combo / going_pedigree 追加 (2026-04-11)
#   各既存指数を均等に×0.96 して 0.040 を拠出（各新指数に 0.010 ずつ）。
#   upside_bonus（交互作用項）を inline 追加:
#     last_3f×pedigree / position_advantage×pedigree / jockey×pedigree
#     + speed×pedigree / rotation×pedigree 各 ×0.013333（×1/100）
#   平均ボーナス ≈ 1.67点（全指数が中立50のとき）
# v14: 速度指数バグ修正 (2026-04-11) ← 全期間バックフィル必須
#   SpeedIndexCalculator.calculate_batch が _preload_standard_times を呼んでいなかった。
#   基準タイムキャッシュが常に空 → 全馬の速度指数が 50.0（デフォルト値）に固定されていた。
#   修正: calculate_batch の先頭で await self._preload_standard_times(race_id) を呼ぶ。
#   ※ v13 以前のバックフィルデータは速度指数がすべて 50.0 で無効。再算出が必要。
# v15: 芝スプリント限定ウェイトチューニング (2026-04-12)
#   バックテスト分析（2025年全年/3,106レース）でセグメント別スピアマン相関を測定:
#   ① 芝スプリント(〜1400m): 展開ρ=+0.02（逆効果） → pace=0、速度+騎手に再配分
#      シミュレーション: 芝スプリント ROI 77.1% → 90.7% (+13.6%)
#   ※ ダートルール(paddock有効化)・長距離ルールは全体ROIを悪化させたため除外
#      ダート: ダートマイル -7.2%、ダートスプリント -4.2%（paddock重みが逆効果）
#      長距離: 芝長距離 +0.0%（効果なし）、その他長距離 -6.0%
# v16: 再帰的改善 Cycle#6 採用 (2026-04-13)
#   roi目標 / Nelder-Mead / λ=3.0 / top-n=0 (交互作用項なし)
#   テスト期間: 1位単勝ROI 81.8%→86.1% (+4.3%), 穴馬ROI 67.6%→85.2% (+17.6%)
#   pedigree 8.9%→15.7% (+6.8%), jockey 11.9%→10.4% (-1.5%)
# v17: 再帰的改善 Cycle#10 採用 (2026-04-14)
#   roi目標 / λ=3.0 / v15データ(3年分) / top-n=0
#   テスト期間: 1位単勝ROI 86.1%→89.1% (+3.0%), 穴馬ROI 85.2%→95.8% (+10.6%), 3着内率 +0.3%
#   pedigree 15.7%→20.2% (+4.5%), course_aptitude 11.8%→10.4% (-1.4%)
COMPOSITE_VERSION = 17

# 未実装指数のデフォルト値
DEFAULT_INDEX = SPEED_INDEX_MEAN  # 50.0

# 総合指数クリップ範囲
INDEX_MIN = 0.0
INDEX_MAX = 100.0


def _segment_weights(surface: str | None, distance: int | None) -> dict:
    """セグメント（馬場×距離）別にウェイトを調整して返す。

    バックテスト分析（v14 2025年全年/3,106レース）のスピアマン相関に基づく:

    芝スプリント (surface starts with "芝", distance ≤ 1400):
       - 展開指数 ρ = +0.02 (逆効果) → pace = 0
       - 解放した予算をスピード(70%)・騎手(30%)に再配分
       - シミュレーション: ROI 77.1% → 90.7% (+13.6%)

    ※ 以下は検討したが除外:
       - ダートルール(paddock有効化): ダートマイル -7.2%、ダートスプリント -4.2% で逆効果
       - 長距離ルール: 芝長距離 +0.0%（効果なし）、その他長距離 -6.0%

    Args:
        surface: "芝" / "ダート" / "障害" 等（Race.surface）
        distance: 距離（m）

    Returns:
        調整済みウェイト dict（INDEX_WEIGHTS をコピーして変更）
    """
    w = dict(INDEX_WEIGHTS)

    is_turf_sprint = (
        isinstance(surface, str) and surface.startswith("芝")
        and distance is not None and distance <= 1400
    )

    # 芝スプリント: 展開ウェイトを0にしてスピード・騎手に再配分
    if is_turf_sprint:
        freed = w["pace"]                        # 0.02132
        w["pace"] = 0.0
        w["speed"] += freed * 0.70               # +0.01492
        w["jockey_trainer"] += freed * 0.30      # +0.00640

    # 負値クランプ（念のため）
    for k in w:
        if w[k] < 0.0:
            w[k] = 0.0

    return w

# Softmax 温度パラメータ（composite_index 10点差 → 約2.7倍の確率比）
SOFTMAX_TEMPERATURE = 10.0


class CompositeIndexCalculator:
    """総合指数算出Agent。

    全指数Agentを統括し、weighted sum で総合指数を算出して
    calculated_indices テーブルへ保存する。
    """

    def __init__(self, db: AsyncSession) -> None:
        """初期化。

        Args:
            db: SQLAlchemy 非同期セッション
        """
        self.db = db
        self._speed = SpeedIndexCalculator(db)
        self._last3f = Last3FIndexCalculator(db)
        self._course = CourseAptitudeCalculator(db)
        self._frame = FrameBiasCalculator(db)
        self._rotation = RotationIndexCalculator(db)
        self._jockey = JockeyIndexCalculator(db)
        self._pace = PaceIndexCalculator(db)
        self._pedigree = PedigreeIndexCalculator(db)
        self._training = TrainingIndexCalculator(db)
        self._anagusa = AnagusaIndexCalculator(db)
        self._paddock = PaddockIndexCalculator(db)
        self._rebound = ReboundIndexCalculator(db)
        self._rivals_growth = RivalsGrowthIndexCalculator(db)
        self._career_phase = CareerPhaseIndexCalculator(db)
        self._distance_change = DistanceChangeIndexCalculator(db)
        self._jockey_trainer_combo = JockeyTrainerComboIndexCalculator(db)
        self._going_pedigree = GoingPedigreeIndexCalculator(db)

    # ------------------------------------------------------------------
    # 公開インターフェース
    # ------------------------------------------------------------------

    async def calculate_and_save(self, race_id: int) -> list[dict]:
        """レース全馬の総合指数を算出して calculated_indices へ upsert する。

        Args:
            race_id: DB の races.id

        Returns:
            [{"horse_id": int, "composite_index": float, ...}, ...] 算出結果リスト
        """
        race_result = await self.db.execute(select(Race).where(Race.id == race_id))
        race = race_result.scalar_one_or_none()
        if not race:
            logger.warning(f"Race not found: race_id={race_id}")
            return []

        entries_result = await self.db.execute(select(RaceEntry).where(RaceEntry.race_id == race_id))
        entries = entries_result.scalars().all()
        if not entries:
            logger.info(f"No entries for race_id={race_id}")
            return []

        logger.info(
            f"総合指数算出開始: race_id={race_id} "
            f"({race.date} {race.course_name} R{race.race_number} "
            f"{race.distance}m {race.surface}) {len(entries)}頭"
        )

        # 各指数を一括算出（N+1回避: calculate_batch を使用）
        # speed は rotation のタイムボーナスに使うため先に算出する
        speed_map = await self._speed.calculate_batch(race_id)
        last3f_map = await self._last3f.calculate_batch(race_id)
        course_map = await self._course.calculate_batch(race_id)
        frame_map = await self._frame.calculate_batch(race_id)
        rotation_map = await self._rotation.calculate_batch(race_id, speed_map=speed_map)
        jockey_map = await self._jockey.calculate_batch(race_id)
        pace_map = await self._pace.calculate_batch(race_id)
        pedigree_map = await self._pedigree.calculate_batch(race_id)
        training_map = await self._training.calculate_batch(race_id)
        anagusa_map = await self._anagusa.calculate_batch(race_id)
        paddock_map = await self._paddock.calculate_batch(race_id)
        rebound_map = await self._rebound.calculate_batch(race_id)
        rivals_growth_map = await self._rivals_growth.calculate_batch(race_id)
        career_phase_map = await self._career_phase.calculate_batch(race_id)
        distance_change_map = await self._distance_change.calculate_batch(race_id)
        jockey_trainer_combo_map = await self._jockey_trainer_combo.calculate_batch(race_id)
        going_pedigree_map = await self._going_pedigree.calculate_batch(race_id)

        # セグメント別ウェイト（レース単位で1回だけ計算）
        seg_weights = _segment_weights(race.surface, race.distance)

        results = []
        for entry in entries:
            hid = entry.horse_id
            row = self._compute_composite(
                horse_id=hid,
                speed=speed_map.get(hid, DEFAULT_INDEX),
                last3f=last3f_map.get(hid, DEFAULT_INDEX),
                course_aptitude=course_map.get(hid, DEFAULT_INDEX),
                position_advantage=frame_map.get(hid, DEFAULT_INDEX),
                rotation=rotation_map.get(hid, DEFAULT_INDEX),
                jockey=jockey_map.get(hid, DEFAULT_INDEX),
                pace=pace_map.get(hid, DEFAULT_INDEX),
                pedigree=pedigree_map.get(hid, DEFAULT_INDEX),
                training=training_map.get(hid, DEFAULT_INDEX),
                anagusa=anagusa_map.get(hid, DEFAULT_INDEX),
                paddock=paddock_map.get(hid, DEFAULT_INDEX),
                rebound=rebound_map.get(hid, DEFAULT_INDEX),
                rivals_growth=rivals_growth_map.get(hid, DEFAULT_INDEX),
                career_phase=career_phase_map.get(hid, DEFAULT_INDEX),
                distance_change=distance_change_map.get(hid, DEFAULT_INDEX),
                jockey_trainer_combo=jockey_trainer_combo_map.get(hid, DEFAULT_INDEX),
                going_pedigree=going_pedigree_map.get(hid, DEFAULT_INDEX),
                weights=seg_weights,
            )
            results.append({"horse_id": hid, **row})

        # 全馬の指数が揃ってから勝率・複勝率を算出（softmax + Harville）
        self._attach_probabilities(results)

        # バルク upsert（1 SELECT/レース + add_all で馬ごと N 往復を回避）
        await self._bulk_upsert_for_race(race_id, results)

        logger.info(f"総合指数算出完了: {len(results)} 頭")
        return results

    async def calculate_batch_for_date(self, date: str) -> list[dict]:
        """指定日の全レース・全馬の総合指数を算出して保存する。

        レース単位で最大 _RACE_CONCURRENCY 並列処理を行う。
        SireStatsCache はメインセッションで1回だけ初期化し、
        ワーカーインスタンス間でクラス変数経由で共有する（再初期化コスト回避）。

        Args:
            date: "YYYYMMDD" 形式の日付

        Returns:
            全馬分の算出結果リスト（race_id・horse_id 付き）
        """
        _JRA_COURSE_CODES = {"01", "02", "03", "04", "05", "06", "07", "08", "09", "10"}
        races_result = await self.db.execute(
            select(Race)
            .where(Race.date == date, Race.course.in_(list(_JRA_COURSE_CODES)))
            .order_by(Race.race_number)
        )
        races = races_result.scalars().all()
        if not races:
            logger.warning(f"指定日にレースなし: {date}")
            return []

        # SireStatsCache をメインセッションで事前ロード（ワーカーの再初期化を防ぐ）
        await self._pedigree._cache.ensure_loaded()
        if PedigreeIndexCalculator._shared_cache is None:
            PedigreeIndexCalculator._shared_cache = self._pedigree._cache

        # レース並列処理: 各レースを独立セッションで同時処理（最大 _RACE_CONCURRENCY）
        _RACE_CONCURRENCY = 4
        sem = asyncio.Semaphore(_RACE_CONCURRENCY)

        async def _process_race(race: Race) -> list[dict]:
            async with sem:
                async with AsyncSessionLocal() as session:
                    calc = CompositeIndexCalculator(session)
                    rows = await calc.calculate_and_save(race.id)
                    await session.commit()
                    for row in rows:
                        row["race_id"] = race.id
                        row["date"] = date
                        row["course_name"] = race.course_name
                        row["race_number"] = race.race_number
                        row["race_name"] = race.race_name
                    return rows

        raw = await asyncio.gather(
            *[_process_race(r) for r in races], return_exceptions=True
        )
        all_results: list[dict] = []
        for i, result in enumerate(raw):
            if isinstance(result, Exception):
                logger.error(f"レース並列処理エラー race_id={races[i].id} ({date}): {result}")
            else:
                all_results.extend(result)  # type: ignore[arg-type]

        return all_results

    # ------------------------------------------------------------------
    # 内部メソッド
    # ------------------------------------------------------------------

    def _compute_composite(
        self,
        horse_id: int,
        speed: float,
        last3f: float,
        course_aptitude: float,
        position_advantage: float,
        rotation: float,
        jockey: float,
        pace: float,
        pedigree: float,
        training: float,
        anagusa: float,
        paddock: float,
        rebound: float = DEFAULT_INDEX,
        rivals_growth: float = DEFAULT_INDEX,
        career_phase: float = DEFAULT_INDEX,
        distance_change: float = DEFAULT_INDEX,
        jockey_trainer_combo: float = DEFAULT_INDEX,
        going_pedigree: float = DEFAULT_INDEX,
        weights: dict | None = None,
    ) -> dict:
        """各指数から総合指数を算出する。

        未実装指数（パドック）はニュートラル値（50.0）で補完する。
        weights を指定するとセグメント別ウェイトを使用する。

        Args:
            horse_id: 馬ID（ログ用）
            speed: スピード指数
            last3f: 後3ハロン指数
            course_aptitude: コース適性指数
            position_advantage: 枠順バイアス指数
            rotation: ローテーション指数
            jockey: 騎手指数
            pace: 展開指数
            pedigree: 血統指数
            training: 調教指数（タイムトレンド近似）
            anagusa: 穴ぐさ指数（sekito.anagusa ピック実績ベース）
            paddock: パドック指数（sekito.netkeiba p_rank ベース、データなし=50）
            rebound: 巻き返し指数（前走不利+着順乖離、中立=50）
            rivals_growth: 上昇相手指数（過去に負かした相手馬の後続活躍度、中立=50）
            career_phase: 成長曲線指数（直近N走トレンドと馬齢フェーズ、中立=50）
            distance_change: 距離変更適性指数（延長/短縮パターン別成績、中立=50）
            jockey_trainer_combo: 騎手×厩舎コンビ指数（コンビ勝率 vs 単独騎手勝率、中立=50）
            going_pedigree: 重馬場×血統指数（重/不良馬場での父系統適性、中立=50）
            weights: セグメント別ウェイト dict（None のとき INDEX_WEIGHTS を使用）

        Returns:
            各指数と総合指数を含む dict
        """
        w = weights if weights is not None else INDEX_WEIGHTS

        base_composite = (
            speed * w["speed"]
            + last3f * w["last_3f"]
            + course_aptitude * w["course_aptitude"]
            + pace * w["pace"]
            + jockey * w["jockey_trainer"]
            + pedigree * w["pedigree"]
            + rotation * w["rotation"]
            + training * w["training"]
            + position_advantage * w["position_advantage"]
            + anagusa * w["anagusa"]
            + paddock * w["paddock"]
            + rebound * w["disadvantage_bonus"]
            + rivals_growth * w["rivals_growth"]
            + career_phase * w["career_phase"]
            + distance_change * w["distance_change"]
            + jockey_trainer_combo * w["jockey_trainer_combo"]
            + going_pedigree * w["going_pedigree"]
        )

        # 交互作用項ボーナス（top5 pedigree interactions from optimization）
        _INTER_W = 0.013333
        upside_bonus = (
            last3f * pedigree / 100.0 * _INTER_W
            + position_advantage * pedigree / 100.0 * _INTER_W
            + jockey * pedigree / 100.0 * _INTER_W
            + speed * pedigree / 100.0 * _INTER_W
            + rotation * pedigree / 100.0 * _INTER_W
        )

        composite = base_composite + upside_bonus
        composite = round(max(INDEX_MIN, min(INDEX_MAX, composite)), 1)

        return {
            "speed_index": round(speed, 1),
            "last3f_index": round(last3f, 1),
            "course_aptitude": round(course_aptitude, 1),
            "position_advantage": round(position_advantage, 1),
            "rotation_index": round(rotation, 1),
            "jockey_index": round(jockey, 1),
            "pace_index": round(pace, 1),
            "pedigree_index": round(pedigree, 1),
            "training_index": round(training, 1),
            "anagusa_index": round(anagusa, 1),
            "paddock_index": round(paddock, 1),
            "rebound_index": round(rebound, 1),
            "rivals_growth_index": round(rivals_growth, 1),
            "career_phase_index": round(career_phase, 1),
            "distance_change_index": round(distance_change, 1),
            "jockey_trainer_combo_index": round(jockey_trainer_combo, 1),
            "going_pedigree_index": round(going_pedigree, 1),
            "upside_bonus": round(upside_bonus, 3),
            "composite_index": composite,
        }

    @staticmethod
    def _attach_probabilities(results: list[dict]) -> None:
        """全馬の composite_index から勝率・複勝率を算出して results に追記する。

        勝率: Softmax(composite_index / SOFTMAX_TEMPERATURE)
        複勝率: Harville 公式で上位3着以内確率を近似

        Args:
            results: _compute_composite の戻り値リスト（in-place 更新）
        """
        scores = [r["composite_index"] for r in results]
        win_probs = CompositeIndexCalculator._softmax(scores)
        place_probs = CompositeIndexCalculator._harville_place_probs(win_probs)

        for row, wp, pp in zip(results, win_probs, place_probs):
            row["win_probability"] = round(wp, 4)
            row["place_probability"] = round(pp, 4)

    @staticmethod
    def _softmax(scores: list[float]) -> list[float]:
        """Softmax 変換で勝率を算出する。数値安定のため max を引く。"""
        t = SOFTMAX_TEMPERATURE
        shifted = [s / t for s in scores]
        max_s = max(shifted)
        exps = [math.exp(s - max_s) for s in shifted]
        total = sum(exps)
        return [e / total for e in exps]

    @staticmethod
    def _harville_place_probs(win_probs: list[float]) -> list[float]:
        """Harville 公式で各馬の複勝確率を算出する。

        JRAルール:
          - 8頭以上: 3着以内 → P(i) = P(1着) + P(2着) + P(3着)
          - 8頭未満: 2着以内 → P(i) = P(1着) + P(2着)

        計算量: O(n^3) だが n≤18（JRA最大出走数）のため問題なし。
        """
        n = len(win_probs)

        # 1頭: 単勝的中 = 複勝的中
        if n == 1:
            return [1.0]

        # JRAルール: 8頭未満は2着払い、8頭以上は3着払い
        # ただし頭数 ≤ 払戻対象着順 の場合は全馬複勝対象（1.0）
        place_within = 3 if n >= 8 else 2
        if n <= place_within:
            return [1.0] * n

        place_probs = []

        for i in range(n):
            pi = win_probs[i]

            # 2着確率: Σ_{j≠i} P(j 1着) × P(i | j 除外後)
            p2 = 0.0
            for j in range(n):
                if j == i:
                    continue
                denom_j = 1.0 - win_probs[j]
                if denom_j <= 1e-9:
                    continue
                p2 += win_probs[j] * (pi / denom_j)

            # 3着確率: Σ_{j≠i} Σ_{k≠i,j} P(j 1着) × P(k | j 除外後) × P(i | j,k 除外後)
            # 8頭未満は2着払いのため p3 は加算しない
            p3 = 0.0
            if n >= 8:
                for j in range(n):
                    if j == i:
                        continue
                    denom_j = 1.0 - win_probs[j]
                    if denom_j <= 1e-9:
                        continue
                    for k in range(n):
                        if k == i or k == j:
                            continue
                        p_k_given_j = win_probs[k] / denom_j
                        denom_jk = 1.0 - win_probs[j] - win_probs[k]
                        if denom_jk <= 1e-9:
                            continue
                        p3 += win_probs[j] * p_k_given_j * (pi / denom_jk)

            place_probs.append(min(pi + p2 + p3, 1.0))

        return place_probs

    async def _bulk_upsert_for_race(self, race_id: int, results: list[dict]) -> None:
        """レース全馬分を一括 upsert する（バックフィル高速化用）。

        従来の馬ごと SELECT + INSERT/UPDATE（N往復）を
        1 SELECT（レース全馬一括）+ add_all（新規のみ）に削減する。

        Args:
            race_id: DB の races.id
            results: calculate_and_save の results リスト（horse_id キーを含む）
        """
        if not results:
            return

        existing_result = await self.db.execute(
            select(CalculatedIndex).where(
                CalculatedIndex.race_id == race_id,
                CalculatedIndex.version == COMPOSITE_VERSION,
            )
        )
        existing_map: dict[int, CalculatedIndex] = {
            r.horse_id: r for r in existing_result.scalars().all()
        }

        def _d(v: object) -> Decimal | None:
            return Decimal(str(v)) if v is not None else None

        new_records: list[CalculatedIndex] = []
        for row in results:
            hid = row["horse_id"]
            kwargs = {
                "speed_index": _d(row.get("speed_index")),
                "last_3f_index": _d(row.get("last3f_index")),
                "course_aptitude": _d(row.get("course_aptitude")),
                "position_advantage": _d(row.get("position_advantage")),
                "rotation_index": _d(row.get("rotation_index")),
                "jockey_index": _d(row.get("jockey_index")),
                "pace_index": _d(row.get("pace_index")),
                "pedigree_index": _d(row.get("pedigree_index")),
                "training_index": _d(row.get("training_index")),
                "anagusa_index": _d(row.get("anagusa_index")),
                "paddock_index": _d(row.get("paddock_index")),
                "rebound_index": _d(row.get("rebound_index")),
                "rivals_growth_index": _d(row.get("rivals_growth_index")),
                "career_phase_index": _d(row.get("career_phase_index")),
                "distance_change_index": _d(row.get("distance_change_index")),
                "jockey_trainer_combo_index": _d(row.get("jockey_trainer_combo_index")),
                "going_pedigree_index": _d(row.get("going_pedigree_index")),
                "composite_index": _d(row.get("composite_index")),
                "win_probability": _d(row.get("win_probability")),
                "place_probability": _d(row.get("place_probability")),
            }
            if hid in existing_map:
                existing = existing_map[hid]
                for attr, val in kwargs.items():
                    setattr(existing, attr, val)
                existing.calculated_at = datetime.now()
            else:
                new_records.append(
                    CalculatedIndex(
                        race_id=race_id,
                        horse_id=hid,
                        version=COMPOSITE_VERSION,
                        **kwargs,
                    )
                )

        if new_records:
            self.db.add_all(new_records)

    async def _upsert(self, race_id: int, horse_id: int, data: dict) -> None:
        """calculated_indices へ upsert する（同 race_id + horse_id + version は上書き）。

        SELECT + UPDATE/INSERT の2段階。重複行が存在する場合は最初の1行を更新し、
        余分な行は削除して一意性を回復する。
        単一馬の更新（リアルタイム再算出など）に使用。バックフィルには _bulk_upsert_for_race を使うこと。

        Args:
            race_id: DB の races.id
            horse_id: DB の horses.id
            data: _compute_composite の戻り値
        """
        existing_result = await self.db.execute(
            select(CalculatedIndex).where(
                and_(
                    CalculatedIndex.race_id == race_id,
                    CalculatedIndex.horse_id == horse_id,
                    CalculatedIndex.version == COMPOSITE_VERSION,
                )
            )
        )
        all_existing = existing_result.scalars().all()

        # 重複行がある場合、余分な行を削除して1行に正規化
        if len(all_existing) > 1:
            for dup in all_existing[1:]:
                await self.db.delete(dup)
            await self.db.flush()

        existing = all_existing[0] if all_existing else None

        def _d(key: str) -> Decimal | None:
            v = data.get(key)
            return Decimal(str(v)) if v is not None else None

        if existing:
            existing.speed_index = _d("speed_index")
            existing.last_3f_index = _d("last3f_index")
            existing.course_aptitude = _d("course_aptitude")
            existing.position_advantage = _d("position_advantage")
            existing.rotation_index = _d("rotation_index")
            existing.jockey_index = _d("jockey_index")
            existing.pace_index = _d("pace_index")
            existing.pedigree_index = _d("pedigree_index")
            existing.training_index = _d("training_index")
            existing.anagusa_index = _d("anagusa_index")
            existing.paddock_index = _d("paddock_index")
            existing.rebound_index = _d("rebound_index")
            existing.rivals_growth_index = _d("rivals_growth_index")
            existing.career_phase_index = _d("career_phase_index")
            existing.distance_change_index = _d("distance_change_index")
            existing.jockey_trainer_combo_index = _d("jockey_trainer_combo_index")
            existing.going_pedigree_index = _d("going_pedigree_index")
            existing.composite_index = _d("composite_index")
            existing.win_probability = _d("win_probability")
            existing.place_probability = _d("place_probability")
            existing.calculated_at = datetime.now()
        else:
            record = CalculatedIndex(
                race_id=race_id,
                horse_id=horse_id,
                version=COMPOSITE_VERSION,
                speed_index=_d("speed_index"),
                last_3f_index=_d("last3f_index"),
                course_aptitude=_d("course_aptitude"),
                position_advantage=_d("position_advantage"),
                rotation_index=_d("rotation_index"),
                jockey_index=_d("jockey_index"),
                pace_index=_d("pace_index"),
                pedigree_index=_d("pedigree_index"),
                training_index=_d("training_index"),
                anagusa_index=_d("anagusa_index"),
                paddock_index=_d("paddock_index"),
                rebound_index=_d("rebound_index"),
                rivals_growth_index=_d("rivals_growth_index"),
                career_phase_index=_d("career_phase_index"),
                distance_change_index=_d("distance_change_index"),
                jockey_trainer_combo_index=_d("jockey_trainer_combo_index"),
                going_pedigree_index=_d("going_pedigree_index"),
                composite_index=_d("composite_index"),
                win_probability=_d("win_probability"),
                place_probability=_d("place_probability"),
            )
            self.db.add(record)
