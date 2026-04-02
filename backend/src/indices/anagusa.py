"""穴ぐさ指数算出Agent

sekito.anagusa テーブルから穴ぐさピック情報を取得し、
バックテスト実績に基づくバイアス補正を加えた期待度スコアを算出する。

穴ぐさとは、人気より実力があり期待値の高い馬をピックアップした情報。
レース当日 07:10 頃に更新される。

スコアリング設計（バックテスト 2024〜2026年）:
  全体複勝率: 15.5%
  rank A:    19.4% → base_score=75
  rank B:    14.9% → base_score=60
  rank C:    11.8% → base_score=50
  ピックなし: 50.0  （ニュートラル値）

バイアス補正（各次元の複勝率 vs 全体平均のズレをスコアに加算）:
  - コース補正  : 最大 ±2.5 点（scale=5.0）
  - 距離×馬場補正: 最大 ±5.0 点（scale=8.0）
  - 頭数補正   : 最大 ±2.0 点（scale=5.0）

最終スコア: clip(base + course_adj + surface_dist_adj + head_adj, 0, 100)
"""

from __future__ import annotations

import logging
from datetime import date as _date

from sqlalchemy import select, text

from ..db.models import Race, RaceEntry
from .base import IndexCalculator

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# sekito.anagusa コードマッピング (sekito → JRA-VAN 2桁課コード)
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
# バックテスト実績値（2024〜2026年）
# --------------------------------------------------------------------------

# 全体複勝率（%）
OVERALL_PLACE_RATE: float = 15.5

# rank → ベーススコア
# 複勝率実績: A=19.4%, B=14.9%, C=11.8%, 全体=15.5%
# C は全体平均を下回るためニュートラル(50)より低い値に設定
RANK_BASE_SCORES: dict[str, float] = {
    "A": 75.0,
    "B": 60.0,
    "C": 42.0,
}

# ピックなし or 不明 rank のスコア
DEFAULT_SCORE: float = 50.0

# コース（JRA 2桁コード）→ 複勝率（%）
COURSE_PLACE_RATES: dict[str, float] = {
    "01": 14.4,  # 札幌
    "02": 14.7,  # 函館
    "03": 17.4,  # 福島
    "04": 14.7,  # 新潟
    "05": 14.3,  # 東京
    "06": 15.1,  # 中山
    "07": 14.4,  # 中京
    "08": 17.3,  # 京都
    "09": 16.5,  # 阪神
    "10": 14.6,  # 小倉
}

# (surface, dist_band) → 複勝率（%）
# dist_band: 1=〜1200m, 2=1201〜1600m, 3=1601〜2000m, 4=2001m〜
SURFACE_DIST_PLACE_RATES: dict[tuple[str, int], float] = {
    ("ダ", 1): 14.0,
    ("ダ", 2): 15.2,
    ("ダ", 3): 16.9,
    ("ダ", 4): 13.9,
    ("芝", 1): 14.6,
    ("芝", 2): 16.6,
    ("芝", 3): 15.9,
    ("芝", 4): 13.1,
}

# 頭数帯 → 複勝率（%）
# head_band: 1=〜8頭, 2=9〜13頭, 3=14頭〜
HEAD_PLACE_RATES: dict[int, float] = {
    1: OVERALL_PLACE_RATE,  # サンプル少 → 全体平均で代用
    2: 17.1,
    3: 15.0,
}

# バイアス補正スケール（各次元のズレを何倍でスコアに変換するか）
COURSE_SCALE: float = 5.0
SURFACE_DIST_SCALE: float = 8.0
HEAD_SCALE: float = 5.0


class AnagusaIndexCalculator(IndexCalculator):
    """穴ぐさ指数算出Agent。

    sekito.anagusa テーブルを参照し、競馬場・距離・馬場・頭数の
    バイアスを加えた期待度スコアを返す。

    ピックなし馬はニュートラル値 50.0 を返す。
    """

    async def calculate(self, race_id: int, horse_id: int) -> float:
        """単一馬の穴ぐさ指数を算出する。

        Args:
            race_id: DB の races.id
            horse_id: DB の horses.id

        Returns:
            穴ぐさ指数（0〜100）
        """
        batch = await self.calculate_batch(race_id)
        return batch.get(horse_id, DEFAULT_SCORE)

    async def calculate_batch(self, race_id: int) -> dict[int, float]:
        """レース全馬の穴ぐさ指数を一括算出する。

        Args:
            race_id: DB の races.id

        Returns:
            {horse_id: score} — ピックなし馬は DEFAULT_SCORE(50.0)
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

        # sekito.anagusa からピック取得
        picks = await self._fetch_picks(race)

        # バイアス係数を計算
        course_adj = self._course_adj(race.course)
        surface_dist_adj = self._surface_dist_adj(race.surface, race.distance)
        head_adj = self._head_adj(race.head_count)

        result: dict[int, float] = {}
        for entry in entries:
            rank: str | None = picks.get(entry.horse_number)
            base = RANK_BASE_SCORES.get(rank, DEFAULT_SCORE) if rank else DEFAULT_SCORE
            score = base + course_adj + surface_dist_adj + head_adj
            score = round(max(0.0, min(100.0, score)), 1)
            result[entry.horse_id] = score

        picked_count = sum(1 for v in result.values() if v != DEFAULT_SCORE)
        logger.debug(
            f"穴ぐさ指数: race_id={race_id} "
            f"picks={picked_count}/{len(entries)} "
            f"course_adj={course_adj:+.1f} "
            f"sd_adj={surface_dist_adj:+.1f} "
            f"head_adj={head_adj:+.1f}"
        )
        return result

    # ------------------------------------------------------------------
    # 内部メソッド
    # ------------------------------------------------------------------

    async def _fetch_picks(self, race: Race) -> dict[int, str]:
        """sekito.anagusa からこのレースのピック情報を取得する。

        Args:
            race: Race モデルインスタンス

        Returns:
            {horse_number: rank} — ピックされた馬番→rank(A/B/C)
        """
        # sekito.anagusa は date型、keiba.races.date は YYYYMMDD文字列
        # asyncpg は DATE カラムに datetime.date オブジェクトが必要（文字列不可）
        race_date = _date(int(race.date[:4]), int(race.date[4:6]), int(race.date[6:8]))

        # sekito course_code → JRA 2桁コードの逆引き
        jra_to_sekito = {v: k for k, v in SEKITO_COURSE_MAP.items()}
        sekito_code = jra_to_sekito.get(race.course)
        if not sekito_code:
            return {}

        sql = text(
            """
            SELECT horse_no, rank
            FROM sekito.anagusa
            WHERE date = :race_date
              AND course_code = :course_code
              AND race_no = :race_no
            """
        )
        result = await self.db.execute(
            sql,
            {
                "race_date": race_date,
                "course_code": sekito_code,
                "race_no": race.race_number,
            },
        )
        rows = result.fetchall()

        return {row.horse_no: row.rank for row in rows if row.rank in RANK_BASE_SCORES}

    @staticmethod
    def _course_adj(course: str) -> float:
        """コードのバイアス補正値を返す。

        Args:
            course: JRA-VAN 競馬場コード（2桁）

        Returns:
            補正値（プラスで有利コード、マイナスで不利コード）
        """
        rate = COURSE_PLACE_RATES.get(course, OVERALL_PLACE_RATE)
        return (rate - OVERALL_PLACE_RATE) / OVERALL_PLACE_RATE * COURSE_SCALE

    @staticmethod
    def _surface_dist_adj(surface: str, distance: int) -> float:
        """馬場×距離帯のバイアス補正値を返す。

        Args:
            surface: トラック種別（芝/ダ/障）
            distance: 距離（m）

        Returns:
            補正値
        """
        if distance <= 1200:
            band = 1
        elif distance <= 1600:
            band = 2
        elif distance <= 2000:
            band = 3
        else:
            band = 4

        rate = SURFACE_DIST_PLACE_RATES.get((surface, band), OVERALL_PLACE_RATE)
        return (rate - OVERALL_PLACE_RATE) / OVERALL_PLACE_RATE * SURFACE_DIST_SCALE

    @staticmethod
    def _head_adj(head_count: int | None) -> float:
        """頭数帯のバイアス補正値を返す。

        Args:
            head_count: 出走頭数（None の場合は補正なし）

        Returns:
            補正値
        """
        if head_count is None:
            return 0.0
        if head_count <= 8:
            band = 1
        elif head_count <= 13:
            band = 2
        else:
            band = 3

        rate = HEAD_PLACE_RATES.get(band, OVERALL_PLACE_RATE)
        return (rate - OVERALL_PLACE_RATE) / OVERALL_PLACE_RATE * HEAD_SCALE
