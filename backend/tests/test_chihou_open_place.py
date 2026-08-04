"""地方競馬 穴馬複勝（開いたレース）シグナルのユニットテスト。

検証根拠: docs/chihou_darkhorse_feasibility_2026_08_05.md
"""
from __future__ import annotations

import pytest

from src.indices.buy_signal import (
    CHIHOU_OPEN_PLACE_MAX_ODDS,
    CHIHOU_OPEN_PLACE_MIN_ODDS,
    CHIHOU_OPEN_RACE_MAX_TOP3_SHARE,
    chihou_is_open_place,
    chihou_market_top3_share,
)


class TestMarketTop3Share:
    def test_均等オッズなら上位3頭シェアは3頭ぶんの比率になる(self) -> None:
        # 10頭すべて同オッズ → 各馬 0.1 → 上位3頭 = 0.3
        share = chihou_market_top3_share([10.0] * 10)
        assert share == pytest.approx(0.3)

    def test_断然人気がいると上位3頭シェアが跳ね上がる(self) -> None:
        odds = [1.2, 5.0, 8.0, 30.0, 40.0, 50.0, 60.0, 80.0]
        share = chihou_market_top3_share(odds)
        assert share is not None
        assert share > 0.75

    def test_開いたレースは上位3頭シェアが低い(self) -> None:
        odds = [4.5, 5.0, 5.5, 6.0, 7.0, 8.0, 9.0, 12.0, 35.0, 40.0]
        share = chihou_market_top3_share(odds)
        assert share is not None
        assert share < CHIHOU_OPEN_RACE_MAX_TOP3_SHARE

    def test_控除率に依存しない(self) -> None:
        """オッズ全体を一律に割り引いても正規化後のシェアは変わらない。"""
        odds = [2.0, 4.0, 6.0, 10.0, 20.0, 40.0]
        a = chihou_market_top3_share(odds)
        b = chihou_market_top3_share([o * 1.25 for o in odds])
        assert a == pytest.approx(b)

    def test_無効値は捨てる(self) -> None:
        share = chihou_market_top3_share([10.0, None, 10.0, 0.5, 10.0])
        assert share == pytest.approx(1.0)  # 有効3頭のみ → 上位3頭で全部

    def test_有効オッズが3頭未満ならNoneを返す(self) -> None:
        assert chihou_market_top3_share([10.0, 20.0]) is None
        assert chihou_market_top3_share([]) is None
        assert chihou_market_top3_share([None, None, None]) is None


class TestIsOpenPlace:
    OPEN = CHIHOU_OPEN_RACE_MAX_TOP3_SHARE - 0.05
    CLOSED = CHIHOU_OPEN_RACE_MAX_TOP3_SHARE + 0.05

    def test_条件を満たせば該当する(self) -> None:
        assert chihou_is_open_place(35.0, self.OPEN, 10) is True

    def test_閉じたレースは該当しない(self) -> None:
        assert chihou_is_open_place(35.0, self.CLOSED, 10) is False

    @pytest.mark.parametrize("head_count", [None, 1, 7])
    def test_7頭以下は対象外(self, head_count: int | None) -> None:
        """複勝が2着までしか払い戻されないため除外する。"""
        assert chihou_is_open_place(35.0, self.OPEN, head_count) is False

    def test_8頭ちょうどは対象(self) -> None:
        assert chihou_is_open_place(35.0, self.OPEN, 8) is True

    @pytest.mark.parametrize("odds", [None, 1.5, 10.0, 29.9, 50.0, 80.0])
    def test_オッズが帯の外なら該当しない(self, odds: float | None) -> None:
        assert chihou_is_open_place(odds, self.OPEN, 10) is False

    def test_オッズ帯の境界(self) -> None:
        assert chihou_is_open_place(CHIHOU_OPEN_PLACE_MIN_ODDS, self.OPEN, 10) is True
        # 上限は含まない
        assert chihou_is_open_place(CHIHOU_OPEN_PLACE_MAX_ODDS, self.OPEN, 10) is False

    def test_シェア閾値の境界は含まない(self) -> None:
        assert chihou_is_open_place(35.0, CHIHOU_OPEN_RACE_MAX_TOP3_SHARE, 10) is False
        assert chihou_is_open_place(35.0, CHIHOU_OPEN_RACE_MAX_TOP3_SHARE - 1e-9, 10) is True

    def test_シェア不明なら安全側で該当させない(self) -> None:
        assert chihou_is_open_place(35.0, None, 10) is False

    def test_実データ相当の組み合わせ(self) -> None:
        """開いた12頭立てで40倍の馬が該当し、断然人気レースの40倍は該当しない。"""
        open_odds = [4.0, 4.5, 5.0, 6.0, 7.5, 9.0, 11.0, 15.0, 22.0, 30.0, 40.0, 60.0]
        closed_odds = [1.3, 3.5, 6.0, 12.0, 20.0, 30.0, 40.0, 55.0, 70.0, 90.0, 120.0, 150.0]
        open_share = chihou_market_top3_share(open_odds)
        closed_share = chihou_market_top3_share(closed_odds)
        assert chihou_is_open_place(40.0, open_share, len(open_odds)) is True
        assert chihou_is_open_place(40.0, closed_share, len(closed_odds)) is False
