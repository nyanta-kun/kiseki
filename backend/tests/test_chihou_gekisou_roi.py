"""激走馬の単勝・複勝 100円ずつの成績集計の検査（2026-09-29）。

数え方を間違えると、的中していても回収 0 円に見えたり（払戻の取込前に数える）、
取消を外れとして数えたりして、画面の回収率が静かに下振れする。
"""

from __future__ import annotations

import pytest

from src.services.chihou_gekisou_roi import GekisouBet, summarize_gekisou_bets


def test_counts_win_and_place_separately() -> None:
    roi = summarize_gekisou_bets(
        [
            GekisouBet(settled=True, win_payout=8.9, place_payout=2.2),  # 1着
            GekisouBet(settled=True, win_payout=None, place_payout=1.6),  # 3着
            GekisouBet(settled=True, win_payout=None, place_payout=None),  # 着外
        ]
    )
    assert roi.n_bets == 3
    assert (roi.win_hits, roi.win_return) == (1, 890)
    assert (roi.place_hits, roi.place_return) == (2, 380)
    assert roi.win_roi == pytest.approx(890 / 300)
    assert roi.place_roi == pytest.approx(380 / 300)


def test_unsettled_is_not_counted() -> None:
    """未確定（払戻の取込前・取消）は点数にも含めない。"""
    roi = summarize_gekisou_bets(
        [
            GekisouBet(settled=False, win_payout=None, place_payout=None),
            GekisouBet(settled=True, win_payout=None, place_payout=None),
        ]
    )
    assert roi.n_bets == 1
    assert roi.win_roi == 0.0


def test_no_bets_gives_none_not_zero() -> None:
    """0 点のときは 0% ではなく「なし」。0% と出すと全敗に見える。"""
    roi = summarize_gekisou_bets([])
    assert roi.n_bets == 0
    assert roi.win_roi is None
    assert roi.place_roi is None


def test_payout_rounds_to_yen() -> None:
    roi = summarize_gekisou_bets([GekisouBet(settled=True, win_payout=12.34, place_payout=None)])
    assert roi.win_return == 1234
