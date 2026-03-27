"""パドック指数算出Agent

sekito.netkeiba テーブルの p_type / p_rank / p_comment を参照し、
レース発走前のパドック状態を指数化する。

データ更新タイミング: レース発走前 10 分
捕捉率: 約 10〜15%（取得タイミングにより未取得のレースあり）
→ データなし馬はニュートラル値 50.0 を返す。

スコアリング設計（バックテスト 2024〜2026年）:
  p_type  p_rank  複勝率  スコア
  ------  ------  ------  ------
  人気    A       52.3%   85.0   （非常に良好 — 人気馬が絶好気配）
  人気    B       37.9%   70.0   （良好）
  人気    C       20.8%   45.0   （やや不安 — 人気馬として期待値下）
  特注    穴      20.1%   60.0   （ダーク候補が良く見える — 上振れ期待）
  データなし / ランクなし: 50.0  （ニュートラル）

注意:
  - is_paddock=False の場合は paddock データ未取得 → ニュートラル
  - p_rank が A/B/C / 穴 以外（空文字など）→ ニュートラル
  - 発走後の算出では全馬ニュートラルになる場合がある
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db.models import Race, RaceEntry
from .base import IndexCalculator

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# sekito.netkeiba コースマッピング (sekito → JRA-VAN 2桁コード)
# --------------------------------------------------------------------------
SEKITO_COURSE_MAP: dict[str, str] = {
    "JSPK": "01",  # 札幌
    "JHKD": "02",  # 函館
    "JFKS": "03",  # 福島
    "JNGT": "04",  # 新潟
    "JTOK": "05",  # 東京
    "JNKY": "06",  # 中山
    "JCKO": "07",  # 中京
    "JKYO": "08",  # 京都
    "JHSN": "09",  # 阪神
    "JKKR": "10",  # 小倉
}

# --------------------------------------------------------------------------
# パドック評価 → スコアマッピング
# --------------------------------------------------------------------------

# (p_type, p_rank) → スコア
# p_type: "人気" | "特注"
# p_rank: "A" | "B" | "C" | "穴"
PADDOCK_SCORES: dict[tuple[str, str], float] = {
    ("人気", "A"): 85.0,   # 複勝率 52.3%
    ("人気", "B"): 70.0,   # 複勝率 37.9%
    ("人気", "C"): 45.0,   # 複勝率 20.8%（人気馬として期待を下回る）
    ("特注", "穴"): 60.0,  # 複勝率 20.1%（穴馬として高い水準）
}

# データなし / 判定不能のニュートラル値
NEUTRAL_SCORE: float = 50.0


class PaddockIndexCalculator(IndexCalculator):
    """パドック指数算出Agent。

    sekito.netkeiba の is_paddock / p_type / p_rank を参照し、
    レース直前の馬体・気配を指数化する。

    パドックデータが未取得のレース・馬はすべてニュートラル値 50.0 を返す。
    """

    def calculate(self, race_id: int, horse_id: int) -> float:
        """単一馬のパドック指数を算出する。

        Args:
            race_id: DB の races.id
            horse_id: DB の horses.id

        Returns:
            パドック指数（0〜100）
        """
        return self.calculate_batch(race_id).get(horse_id, NEUTRAL_SCORE)

    def calculate_batch(self, race_id: int) -> dict[int, float]:
        """レース全馬のパドック指数を一括算出する。

        Args:
            race_id: DB の races.id

        Returns:
            {horse_id: score} — データなし馬は NEUTRAL_SCORE(50.0)
        """
        race = self.db.query(Race).filter(Race.id == race_id).first()
        if not race:
            logger.warning(f"Race not found: race_id={race_id}")
            return {}

        entries = (
            self.db.query(RaceEntry)
            .filter(RaceEntry.race_id == race_id)
            .all()
        )
        if not entries:
            return {}

        # sekito.netkeiba からパドックデータを取得
        paddock_map = self._fetch_paddock(race)

        result: dict[int, float] = {}
        for entry in entries:
            p_type, p_rank = paddock_map.get(entry.horse_number, (None, None))
            score = PADDOCK_SCORES.get((p_type, p_rank), NEUTRAL_SCORE) if p_type else NEUTRAL_SCORE
            result[entry.horse_id] = round(score, 1)

        available = sum(1 for v in result.values() if v != NEUTRAL_SCORE)
        logger.debug(
            f"パドック指数: race_id={race_id} "
            f"available={available}/{len(entries)}"
        )
        return result

    # ------------------------------------------------------------------
    # 内部メソッド
    # ------------------------------------------------------------------

    def _fetch_paddock(self, race: Race) -> dict[int, tuple[Optional[str], Optional[str]]]:
        """sekito.netkeiba からこのレースのパドックデータを取得する。

        Args:
            race: Race モデルインスタンス

        Returns:
            {horse_number: (p_type, p_rank)} — データなし馬は未収録
        """
        race_date = f"{race.date[:4]}-{race.date[4:6]}-{race.date[6:8]}"

        jra_to_sekito = {v: k for k, v in SEKITO_COURSE_MAP.items()}
        sekito_code = jra_to_sekito.get(race.course)
        if not sekito_code:
            return {}

        sql = text(
            """
            SELECT horse_no, p_type, p_rank
            FROM sekito.netkeiba
            WHERE date = :race_date
              AND course_code = :course_code
              AND race_no = :race_no
              AND is_paddock = true
              AND p_rank IS NOT NULL
              AND p_rank != ''
            """
        )
        rows = self.db.execute(
            sql,
            {
                "race_date": race_date,
                "course_code": sekito_code,
                "race_no": race.race_number,
            },
        ).fetchall()

        return {
            row.horse_no: (row.p_type, row.p_rank)
            for row in rows
            if (row.p_type, row.p_rank) in PADDOCK_SCORES
        }
