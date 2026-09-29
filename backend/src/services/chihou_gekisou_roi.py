"""激走馬を単勝・複勝それぞれ 100円ずつ買った場合の成績を集計する（2026-09-29）。

推奨タブ（`GET /api/chihou/races/gekisou`）の「当日 / 当月」の表に出す。
DB にも FastAPI にも依存しない純関数。

数え方:

- **確定したものだけ数える。** 着順があり、かつそのレースの払戻が取り込まれている馬。
  払戻の取込前に数えると、的中していても回収 0 円に見えて回収率が下振れする
- **出走取消・競走除外は数えない**（返還されるので損益 0 ＝ 買っていないのと同じ）。
  着順が付かないので上の条件で自然に落ちる
- 払戻は「倍率」（100円あたり払戻 ÷ 100）で受け取る。回収率 = 払戻合計 ÷ 点数
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class GekisouBet:
    """1頭ぶんの結果。

    Attributes:
        settled: 確定済みか（着順があり、レースの払戻が取り込み済み）。
        win_payout: 単勝払戻の倍率。1着でなければ None。
        place_payout: 複勝払戻の倍率。複勝圏外なら None。
    """

    settled: bool
    win_payout: float | None
    place_payout: float | None


@dataclass(frozen=True)
class GekisouRoi:
    """単勝・複勝それぞれ 100円ずつ買った場合の成績。"""

    n_bets: int  # 確定した点数（券種ごとに同数）
    win_hits: int
    win_return: int  # 単勝の払戻合計（円）
    place_hits: int
    place_return: int  # 複勝の払戻合計（円）

    @property
    def win_roi(self) -> float | None:
        """単勝回収率（1.0 = 100%）。確定 0 点なら None。"""
        return self.win_return / (self.n_bets * 100) if self.n_bets else None

    @property
    def place_roi(self) -> float | None:
        """複勝回収率（1.0 = 100%）。確定 0 点なら None。"""
        return self.place_return / (self.n_bets * 100) if self.n_bets else None


def summarize_gekisou_bets(bets: Iterable[GekisouBet]) -> GekisouRoi:
    """確定した激走馬について、単勝・複勝を 100円ずつ買った成績を返す。"""
    n = win_hits = place_hits = 0
    win_ret = place_ret = 0
    for b in bets:
        if not b.settled:
            continue
        n += 1
        if b.win_payout is not None:
            win_hits += 1
            win_ret += round(b.win_payout * 100)
        if b.place_payout is not None:
            place_hits += 1
            place_ret += round(b.place_payout * 100)
    return GekisouRoi(
        n_bets=n,
        win_hits=win_hits,
        win_return=win_ret,
        place_hits=place_hits,
        place_return=place_ret,
    )
