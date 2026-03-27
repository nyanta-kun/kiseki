"""開催馬場バイアスサービス

同一開催（同コース・同回）内で日が経つにつれて変化する
内外バイアス・前後バイアスを算出する。

バイアスの仕組み:
  - 開催初日: 芝内側が踏み固められておらず前/内が有利
  - 開催後半: 内側が荒れて外差しが決まりやすくなる
  - 頭数が多いほど影響が大きい（ポジション争いが激化）

使い方（他の Calculator から参照）:
    service = MeetBiasService(db)
    bias = service.get_bias(race)
    # bias.inner_outer: +1.0=強く内有利, 0=中立, -1.0=強く外有利
    # bias.front_back:  +1.0=強く前有利, 0=中立, -1.0=強く後ろ有利
    # bias.sample_count: バイアス算出に使ったレース数（信頼度の目安）

算出ロジック:
  1. jravan_race_id から開催回（kai）を抽出
  2. 同コース・同年・同開催回で対象レースより前の全結果を取得
  3. 内枠(1-4番)と外枠(5-8番+)の勝率を比較 → inner_outer_bias
  4. 4コーナー前方(top35%)と後方(bottom35%)の勝率を比較 → front_back_bias
  5. サンプル数が少ない場合は 0.0（中立）に引き寄せる
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from ..db.models import Race, RaceResult

logger = logging.getLogger(__name__)

# 信頼度計算: この件数以上で bias フル反映
RELIABLE_SAMPLE = 8
# front/backの分類閾値（passing_4 / head_count）
FRONT_THRESHOLD = 0.35   # 上位35%をfront
BACK_THRESHOLD  = 0.65   # 下位35%をback
# 内枠の定義
INNER_FRAMES = {1, 2, 3, 4}


@dataclass
class MeetBias:
    """開催バイアス値。"""
    inner_outer: float = 0.0   # +1.0=内有利 / 0=中立 / -1.0=外有利
    front_back:  float = 0.0   # +1.0=前有利 / 0=中立 / -1.0=後ろ有利
    sample_count: int = 0      # 算出サンプル数（レース数）

    @property
    def reliability(self) -> float:
        """信頼度 (0-1)。サンプルが少ないほど 0 に近づく。"""
        return min(1.0, self.sample_count / RELIABLE_SAMPLE)


def _extract_kai(jravan_race_id: Optional[str]) -> Optional[str]:
    """jravan_race_id から開催回（kai）を抽出する。

    フォーマット: year(4) + monthday(4) + course(2) + kai(2) + day(2) + raceno(2)
    例: "2026032209011012" → kai="01"
    """
    if not jravan_race_id or len(jravan_race_id) < 12:
        return None
    return jravan_race_id[8:10]


def _extract_year(jravan_race_id: Optional[str]) -> Optional[str]:
    """jravan_race_id から年を抽出する。"""
    if not jravan_race_id or len(jravan_race_id) < 4:
        return None
    return jravan_race_id[:4]


class MeetBiasService:
    """開催馬場バイアス算出サービス。

    IndexCalculator ではなく、FrameBiasCalculator や PaceIndexCalculator から
    参照されるユーティリティクラス。セッション内でキャッシュを持つ。
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        # キャッシュ: (course, year, kai) → MeetBias
        self._cache: dict[tuple[str, str, str], MeetBias] = {}

    def get_bias(self, race: Race) -> MeetBias:
        """対象レースの開催バイアスを返す。

        Args:
            race: 対象レース

        Returns:
            MeetBias。バイアス算出不可（jravan_race_id なし等）の場合は
            inner_outer=0.0, front_back=0.0 の中立値を返す。
        """
        kai  = _extract_kai(race.jravan_race_id)
        year = _extract_year(race.jravan_race_id)

        if not kai or not year:
            return MeetBias()

        cache_key = (race.course, year, kai)
        if cache_key in self._cache:
            return self._cache[cache_key]

        bias = self._compute_bias(race.course, year, kai, race.date, race.surface or "")
        self._cache[cache_key] = bias
        return bias

    # ------------------------------------------------------------------
    # 内部メソッド
    # ------------------------------------------------------------------

    def _compute_bias(
        self,
        course: str,
        year: str,
        kai: str,
        before_date: str,
        surface: str,
    ) -> MeetBias:
        """同開催内の過去レース結果からバイアスを算出する。

        Args:
            course: 場コード
            year: 年（4桁文字列）
            kai: 開催回（2桁文字列）
            before_date: この日付より前を対象（YYYYMMDD）
            surface: 馬場種別（バイアス算出は同馬場のみ参照）

        Returns:
            MeetBias
        """
        # 同開催（同コース・同年・同回）内でこのレースより前の全結果を取得
        # jravan_race_id のパターン: year(4) + * + course(2) + kai(2) + *
        pattern = f"{year}____{ course}{kai}%%"

        rows = (
            self.db.query(RaceResult, Race)
            .join(Race, RaceResult.race_id == Race.id)
            .filter(
                Race.course == course,
                Race.surface == surface,
                Race.date < before_date,
                Race.jravan_race_id.like(pattern),
                RaceResult.finish_position.isnot(None),
                RaceResult.frame_number.isnot(None),
                RaceResult.abnormality_code == 0,
            )
            .all()
        )

        if not rows:
            return MeetBias()

        # レース数を数える
        race_ids = {row.Race.id for row in rows}
        sample_count = len(race_ids)

        # ---- 内外バイアス ----
        inner_wins = inner_total = 0
        outer_wins = outer_total = 0

        for row in rows:
            result: RaceResult = row.RaceResult
            frame = result.frame_number
            if frame is None:
                continue
            is_win = int(result.finish_position) == 1
            if frame in INNER_FRAMES:
                inner_total += 1
                if is_win:
                    inner_wins += 1
            else:
                outer_total += 1
                if is_win:
                    outer_wins += 1

        inner_win_rate = inner_wins / inner_total if inner_total > 0 else 0.0
        outer_win_rate = outer_wins / outer_total if outer_total > 0 else 0.0
        denom = inner_win_rate + outer_win_rate
        inner_outer_raw = (
            (inner_win_rate - outer_win_rate) / denom if denom > 1e-9 else 0.0
        )

        # ---- 前後バイアス ----
        front_wins = front_total = 0
        back_wins  = back_total  = 0

        for row in rows:
            result = row.RaceResult
            race   = row.Race
            p4 = result.passing_4
            hc = race.head_count
            if p4 is None or hc is None or hc <= 0:
                continue
            rel = p4 / hc
            is_win = int(result.finish_position) == 1
            if rel <= FRONT_THRESHOLD:
                front_total += 1
                if is_win:
                    front_wins += 1
            elif rel >= BACK_THRESHOLD:
                back_total += 1
                if is_win:
                    back_wins += 1

        front_win_rate = front_wins / front_total if front_total > 0 else 0.0
        back_win_rate  = back_wins  / back_total  if back_total  > 0 else 0.0
        denom2 = front_win_rate + back_win_rate
        front_back_raw = (
            (front_win_rate - back_win_rate) / denom2 if denom2 > 1e-9 else 0.0
        )

        # 信頼度で 0.0 に引き寄せ（サンプル不足時は中立に近づける）
        reliability = min(1.0, sample_count / RELIABLE_SAMPLE)
        inner_outer = round(inner_outer_raw * reliability, 4)
        front_back  = round(front_back_raw  * reliability, 4)

        logger.debug(
            f"MeetBias: course={course} year={year} kai={kai} "
            f"samples={sample_count} "
            f"inner_outer={inner_outer:+.3f} front_back={front_back:+.3f}"
        )

        return MeetBias(
            inner_outer=inner_outer,
            front_back=front_back,
            sample_count=sample_count,
        )
