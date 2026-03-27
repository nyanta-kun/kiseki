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

import logging
import math
from datetime import datetime
from decimal import Decimal

from sqlalchemy import and_
from sqlalchemy.orm import Session

from ..db.models import CalculatedIndex, Race, RaceEntry
from ..utils.constants import INDEX_WEIGHTS, SPEED_INDEX_MEAN
from .anagusa import AnagusaIndexCalculator
from .course_aptitude import CourseAptitudeCalculator
from .paddock import PaddockIndexCalculator
from .frame_bias import FrameBiasCalculator
from .jockey import JockeyIndexCalculator
from .last3f import Last3FIndexCalculator
from .pace import PaceIndexCalculator
from .pedigree import PedigreeIndexCalculator
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
COMPOSITE_VERSION = 7

# 未実装指数のデフォルト値
DEFAULT_INDEX = SPEED_INDEX_MEAN  # 50.0

# 総合指数クリップ範囲
INDEX_MIN = 0.0
INDEX_MAX = 100.0

# Softmax 温度パラメータ（composite_index 10点差 → 約2.7倍の確率比）
SOFTMAX_TEMPERATURE = 10.0


class CompositeIndexCalculator:
    """総合指数算出Agent。

    全指数Agentを統括し、weighted sum で総合指数を算出して
    calculated_indices テーブルへ保存する。
    """

    def __init__(self, db: Session) -> None:
        """初期化。

        Args:
            db: SQLAlchemy セッション
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

    # ------------------------------------------------------------------
    # 公開インターフェース
    # ------------------------------------------------------------------

    def calculate_and_save(self, race_id: int) -> list[dict]:
        """レース全馬の総合指数を算出して calculated_indices へ upsert する。

        Args:
            race_id: DB の races.id

        Returns:
            [{"horse_id": int, "composite_index": float, ...}, ...] 算出結果リスト
        """
        race = self.db.query(Race).filter(Race.id == race_id).first()
        if not race:
            logger.warning(f"Race not found: race_id={race_id}")
            return []

        entries = self.db.query(RaceEntry).filter(RaceEntry.race_id == race_id).all()
        if not entries:
            logger.info(f"No entries for race_id={race_id}")
            return []

        logger.info(
            f"総合指数算出開始: race_id={race_id} "
            f"({race.date} {race.course_name} R{race.race_number} "
            f"{race.distance}m {race.surface}) {len(entries)}頭"
        )

        # 各指数を一括算出（N+1回避: calculate_batch を使用）
        speed_map = self._speed.calculate_batch(race_id)
        last3f_map = self._last3f.calculate_batch(race_id)
        course_map = self._course.calculate_batch(race_id)
        frame_map = self._frame.calculate_batch(race_id)
        rotation_map = self._rotation.calculate_batch(race_id)
        jockey_map = self._jockey.calculate_batch(race_id)
        pace_map = self._pace.calculate_batch(race_id)
        pedigree_map = self._pedigree.calculate_batch(race_id)
        training_map = self._training.calculate_batch(race_id)
        anagusa_map = self._anagusa.calculate_batch(race_id)
        paddock_map = self._paddock.calculate_batch(race_id)

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
            )
            results.append({"horse_id": hid, **row})

        # 全馬の指数が揃ってから勝率・複勝率を算出（softmax + Harville）
        self._attach_probabilities(results)

        for row in results:
            self._upsert(race_id, row["horse_id"], row)

        logger.info(f"総合指数算出完了: {len(results)} 頭")
        return results

    def calculate_batch_for_date(self, date: str) -> list[dict]:
        """指定日の全レース・全馬の総合指数を算出して保存する。

        Args:
            date: "YYYYMMDD" 形式の日付

        Returns:
            全馬分の算出結果リスト（race_id・horse_id 付き）
        """
        races = (
            self.db.query(Race)
            .filter(Race.date == date)
            .order_by(Race.race_number)
            .all()
        )
        if not races:
            logger.warning(f"指定日にレースなし: {date}")
            return []

        all_results = []
        for race in races:
            rows = self.calculate_and_save(race.id)
            for row in rows:
                row["race_id"] = race.id
                row["date"] = date
                row["course_name"] = race.course_name
                row["race_number"] = race.race_number
                row["race_name"] = race.race_name
            all_results.extend(rows)

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
    ) -> dict:
        """各指数から総合指数を算出する。

        未実装指数（パドック）はニュートラル値（50.0）で補完する。

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

        Returns:
            各指数と総合指数を含む dict
        """
        w = INDEX_WEIGHTS

        composite = (
            speed               * w["speed"]
            + last3f            * w["last_3f"]
            + course_aptitude   * w["course_aptitude"]
            + pace              * w["pace"]
            + jockey            * w["jockey_trainer"]
            + pedigree          * w["pedigree"]
            + rotation          * w["rotation"]
            + training          * w["training"]
            + position_advantage * w["position_advantage"]
            + anagusa           * w["anagusa"]
            + paddock           * w["paddock"]
            # disadvantage_bonus は flag ベースで別途加算（未実装）
        )
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
        """Harville 公式で各馬の複勝確率（3着以内）を算出する。

        P(i が3着以内) = P(i が1着) + P(i が2着) + P(i が3着)
        計算量: O(n^3) だが n≤18（JRA最大出走数）のため問題なし。
        """
        n = len(win_probs)
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
            p3 = 0.0
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

    def _upsert(self, race_id: int, horse_id: int, data: dict) -> None:
        """calculated_indices へ upsert する（同 race_id + horse_id + version は上書き）。

        Args:
            race_id: DB の races.id
            horse_id: DB の horses.id
            data: _compute_composite の戻り値
        """
        existing = (
            self.db.query(CalculatedIndex)
            .filter(
                and_(
                    CalculatedIndex.race_id == race_id,
                    CalculatedIndex.horse_id == horse_id,
                    CalculatedIndex.version == COMPOSITE_VERSION,
                )
            )
            .first()
        )

        win_prob = Decimal(str(data["win_probability"])) if "win_probability" in data else None
        place_prob = Decimal(str(data["place_probability"])) if "place_probability" in data else None

        training_val = Decimal(str(data["training_index"])) if "training_index" in data else None
        anagusa_val = Decimal(str(data["anagusa_index"])) if "anagusa_index" in data else None
        paddock_val = Decimal(str(data["paddock_index"])) if "paddock_index" in data else None

        if existing:
            existing.speed_index = Decimal(str(data["speed_index"]))
            existing.last_3f_index = Decimal(str(data["last3f_index"]))
            existing.course_aptitude = Decimal(str(data["course_aptitude"]))
            existing.position_advantage = Decimal(str(data["position_advantage"]))
            existing.rotation_index = Decimal(str(data["rotation_index"]))
            existing.jockey_index = Decimal(str(data["jockey_index"]))
            existing.pace_index = Decimal(str(data["pace_index"]))
            existing.pedigree_index = Decimal(str(data["pedigree_index"]))
            existing.training_index = training_val
            existing.anagusa_index = anagusa_val
            existing.paddock_index = paddock_val
            existing.composite_index = Decimal(str(data["composite_index"]))
            existing.win_probability = win_prob
            existing.place_probability = place_prob
            existing.calculated_at = datetime.now()
        else:
            record = CalculatedIndex(
                race_id=race_id,
                horse_id=horse_id,
                version=COMPOSITE_VERSION,
                speed_index=Decimal(str(data["speed_index"])),
                last_3f_index=Decimal(str(data["last3f_index"])),
                course_aptitude=Decimal(str(data["course_aptitude"])),
                position_advantage=Decimal(str(data["position_advantage"])),
                rotation_index=Decimal(str(data["rotation_index"])),
                jockey_index=Decimal(str(data["jockey_index"])),
                pace_index=Decimal(str(data["pace_index"])),
                pedigree_index=Decimal(str(data["pedigree_index"])),
                training_index=training_val,
                anagusa_index=anagusa_val,
                paddock_index=paddock_val,
                composite_index=Decimal(str(data["composite_index"])),
                win_probability=win_prob,
                place_probability=place_prob,
            )
            self.db.add(record)
