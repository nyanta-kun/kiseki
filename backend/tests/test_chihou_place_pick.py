"""地方競馬 注目馬（人気薄の複勝圏候補）シグナルのユニットテスト。

検証根拠: docs/chihou_darkhorse_feasibility_2026_08_05.md
"""
from __future__ import annotations

import pytest

from src.indices.buy_signal import (
    CHIHOU_OPEN_RACE_MAX_TOP3_SHARE,
    CHIHOU_PICK_MAX_INDEX_RANK,
    CHIHOU_PICK_MAX_PER_RACE,
    CHIHOU_PICK_MIN_POP_RANK,
    chihou_effective_head_count,
    chihou_is_place_pick,
    chihou_market_top3_share,
    chihou_popularity_ranks,
    chihou_select_place_picks,
)


class TestMarketTop3Share:
    def test_均等オッズなら上位3頭シェアは3頭ぶんの比率になる(self) -> None:
        assert chihou_market_top3_share([10.0] * 10) == pytest.approx(0.3)

    def test_断然人気がいると上位3頭シェアが跳ね上がる(self) -> None:
        share = chihou_market_top3_share([1.2, 5.0, 8.0, 30.0, 40.0, 50.0, 60.0, 80.0])
        assert share is not None
        assert share > 0.75

    def test_開いたレースは上位3頭シェアが低い(self) -> None:
        share = chihou_market_top3_share([4.5, 5.0, 5.5, 6.0, 7.0, 8.0, 9.0, 12.0, 35.0, 40.0])
        assert share is not None
        assert share < CHIHOU_OPEN_RACE_MAX_TOP3_SHARE

    def test_控除率に依存しない(self) -> None:
        odds = [2.0, 4.0, 6.0, 10.0, 20.0, 40.0]
        a = chihou_market_top3_share(odds)
        b = chihou_market_top3_share([o * 1.25 for o in odds])
        assert a == pytest.approx(b)

    def test_無効値は捨てる(self) -> None:
        assert chihou_market_top3_share([10.0, None, 10.0, 0.5, 10.0]) == pytest.approx(1.0)

    def test_有効オッズが3頭未満ならNoneを返す(self) -> None:
        assert chihou_market_top3_share([10.0, 20.0]) is None
        assert chihou_market_top3_share([]) is None
        assert chihou_market_top3_share([None, None, None]) is None


class TestPopularityRanks:
    def test_オッズ昇順で人気順位が付く(self) -> None:
        ranks = chihou_popularity_ranks({3: 12.0, 1: 2.5, 5: 40.0, 2: 7.0})
        assert ranks == {1: 1, 2: 2, 3: 3, 5: 4}

    def test_同オッズは馬番の小さい方が上位(self) -> None:
        assert chihou_popularity_ranks({7: 10.0, 2: 10.0, 4: 10.0}) == {2: 1, 4: 2, 7: 3}

    def test_無効オッズは順位を付けない(self) -> None:
        assert chihou_popularity_ranks({1: 3.0, 2: None, 3: 0.5, 4: 8.0}) == {1: 1, 4: 2}

    def test_空なら空を返す(self) -> None:
        assert chihou_popularity_ranks({}) == {}


class TestIsPlacePick:
    OPEN = CHIHOU_OPEN_RACE_MAX_TOP3_SHARE - 0.05
    CLOSED = CHIHOU_OPEN_RACE_MAX_TOP3_SHARE + 0.05

    def test_条件を満たせば該当する(self) -> None:
        assert chihou_is_place_pick(7, 2, self.OPEN, 12) is True

    def test_閉じたレースは該当しない(self) -> None:
        assert chihou_is_place_pick(7, 2, self.CLOSED, 12) is False

    @pytest.mark.parametrize("pop_rank", [None, 1, 3, 5])
    def test_5番人気以内は対象外(self, pop_rank: int | None) -> None:
        """人気馬は「人気薄の複勝圏候補」ではない。"""
        assert chihou_is_place_pick(pop_rank, 2, self.OPEN, 12) is False

    def test_6番人気ちょうどは対象(self) -> None:
        assert chihou_is_place_pick(CHIHOU_PICK_MIN_POP_RANK, 2, self.OPEN, 12) is True

    @pytest.mark.parametrize("index_rank", [None, 6, 7, 10])
    def test_指数6位以下は該当しない(self, index_rank: int | None) -> None:
        assert chihou_is_place_pick(7, index_rank, self.OPEN, 12) is False

    def test_指数5位ちょうどは対象(self) -> None:
        """2026-08-13 に 3位内 → 5位内へ緩和した（網羅率 6.4% → 14.0%）。"""
        assert CHIHOU_PICK_MAX_INDEX_RANK == 5
        assert chihou_is_place_pick(7, CHIHOU_PICK_MAX_INDEX_RANK, self.OPEN, 12) is True

    @pytest.mark.parametrize("head_count", [None, 1, 7])
    def test_7頭以下は対象外(self, head_count: int | None) -> None:
        """複勝が2着までしか払い戻されないため除外する。"""
        assert chihou_is_place_pick(7, 2, self.OPEN, head_count) is False

    def test_8頭ちょうどは対象(self) -> None:
        assert chihou_is_place_pick(7, 2, self.OPEN, 8) is True

    def test_シェア閾値の境界は含まない(self) -> None:
        assert chihou_is_place_pick(7, 2, CHIHOU_OPEN_RACE_MAX_TOP3_SHARE, 12) is False
        assert chihou_is_place_pick(7, 2, CHIHOU_OPEN_RACE_MAX_TOP3_SHARE - 1e-9, 12) is True

    def test_シェア不明なら安全側で該当させない(self) -> None:
        assert chihou_is_place_pick(7, 2, None, 12) is False

    def test_実データ相当の組み合わせ(self) -> None:
        """開いた12頭立てで、市場7番人気だが指数2位の馬が該当する。"""
        odds = {1: 4.0, 2: 4.5, 3: 5.0, 4: 6.0, 5: 7.5, 6: 9.0,
                7: 11.0, 8: 15.0, 9: 22.0, 10: 30.0, 11: 40.0, 12: 60.0}
        share = chihou_market_top3_share(odds.values())
        pops = chihou_popularity_ranks(odds)
        assert share is not None
        assert share < CHIHOU_OPEN_RACE_MAX_TOP3_SHARE
        # 7番馬 = 7番人気。指数5位以内なら該当、6位以下なら非該当
        assert chihou_is_place_pick(pops[7], 2, share, len(odds)) is True
        assert chihou_is_place_pick(pops[7], 5, share, len(odds)) is True
        assert chihou_is_place_pick(pops[7], 6, share, len(odds)) is False
        # 1番馬 = 1番人気は指数1位でも非該当
        assert chihou_is_place_pick(pops[1], 1, share, len(odds)) is False

    def test_断然人気レースでは該当しない(self) -> None:
        """1頭が枠を固定するレースはシェアが高くなり弾かれる。"""
        odds = {1: 1.3, 2: 3.5, 3: 6.0, 4: 12.0, 5: 20.0, 6: 30.0,
                7: 40.0, 8: 55.0, 9: 70.0, 10: 90.0}
        share = chihou_market_top3_share(odds.values())
        pops = chihou_popularity_ranks(odds)
        assert chihou_is_place_pick(pops[7], 1, share, len(odds)) is False


class TestSelectPlacePicks:
    """1レースから採る頭数の上限。

    指数5位内まで緩めたので1レースに最大5頭が適格になりうる。
    目標は「1レースに1〜2頭」なので、絞り込みはここの責務。
    """

    def test_上限は2頭(self) -> None:
        assert CHIHOU_PICK_MAX_PER_RACE == 2
        picks = chihou_select_place_picks([(3, 5), (7, 1), (9, 3), (11, 4)])
        assert picks == [7, 9]

    def test_指数の良い順に採る(self) -> None:
        assert chihou_select_place_picks([(5, 4), (2, 2), (8, 1)]) == [8, 2]

    def test_同順位は馬番の小さい方を優先する(self) -> None:
        assert chihou_select_place_picks([(9, 2), (4, 2), (6, 2)]) == [4, 6]

    def test_候補が上限以下ならそのまま返す(self) -> None:
        assert chihou_select_place_picks([(3, 2)]) == [3]
        assert chihou_select_place_picks([]) == []

    def test_上限を明示指定できる(self) -> None:
        assert chihou_select_place_picks([(3, 1), (5, 2), (7, 3)], max_picks=1) == [3]


class TestEffectiveHeadCount:
    """head_count がレース後にしか入らない問題への対処。

    実測(2026-08-13): 当日 45レース中 head_count 充足は 2、翌日は 0。
    一方 registered_count は発走前から 100% 入る。
    これを見落とすと chihou_is_place_pick が当日は必ず False を返し、
    注目馬の推奨が一頭も出ない（過去データでは head_count が埋まっているため
    検証だけが通ってしまう）。
    """

    def test_確定頭数があればそれを使う(self) -> None:
        assert chihou_effective_head_count(10, 12) == 10

    def test_確定頭数が無ければ登録頭数で代替する(self) -> None:
        assert chihou_effective_head_count(None, 12) == 12

    def test_どちらも無ければNone(self) -> None:
        assert chihou_effective_head_count(None, None) is None

    def test_当日レースでも注目馬判定が通る(self) -> None:
        """head_count=None（当日）でも registered_count があれば判定できる。"""
        share = CHIHOU_OPEN_RACE_MAX_TOP3_SHARE - 0.05
        # 修正前はここが False だった（当日は推奨ゼロ）
        hc = chihou_effective_head_count(None, 12)
        assert chihou_is_place_pick(7, 2, share, hc) is True

    def test_登録頭数が7以下なら通さない(self) -> None:
        share = CHIHOU_OPEN_RACE_MAX_TOP3_SHARE - 0.05
        hc = chihou_effective_head_count(None, 7)
        assert chihou_is_place_pick(7, 2, share, hc) is False
