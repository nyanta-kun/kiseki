"""単勝信頼度ボードの組み立て検査。

指数の無い馬を黙って落とさないこと（落とすと頭数が合わず、しかも画面からは
欠けていることが分からない）と、「オッズ×単勝信頼度」の丸めを固定する。
"""

from __future__ import annotations

from src.services.jra_confidence_board import (
    BoardHorse,
    rank_in_race,
    sort_by_confidence,
)


def _horses(probs: list[float | None]) -> list[BoardHorse]:
    """馬番 1..n / 単勝信頼度 probs の出走馬を作る。"""
    return [
        BoardHorse(horse_number=i + 1, horse_name=f"ウマ{i + 1}", win_odds=None, win_probability=p)
        for i, p in enumerate(probs)
    ]


class TestOddsXConfidence:
    def test_rounds_to_one_decimal(self) -> None:
        h = BoardHorse(1, "ウマ", win_odds=3.2, win_probability=0.284)
        assert h.odds_x_confidence == 0.9  # 3.2 * 0.284 = 0.9088

    def test_none_when_odds_missing(self) -> None:
        assert BoardHorse(1, "ウマ", None, 0.284).odds_x_confidence is None

    def test_none_when_confidence_missing(self) -> None:
        assert BoardHorse(1, "ウマ", 3.2, None).odds_x_confidence is None

    def test_break_even_is_one(self) -> None:
        """1.0 が損益分岐。ここがずれると画面の意味が変わる。"""
        assert BoardHorse(1, "ウマ", 10.0, 0.10).odds_x_confidence == 1.0

    def test_long_shot(self) -> None:
        assert BoardHorse(1, "ウマ", 205.1, 0.004).odds_x_confidence == 0.8


class TestSort:
    def test_descending_by_confidence(self) -> None:
        got = sort_by_confidence(_horses([0.1, 0.5, 0.3]))
        assert [h.horse_number for h in got] == [2, 3, 1]

    def test_keeps_every_horse(self) -> None:
        """並べ替えで頭数が減らないこと。"""
        got = sort_by_confidence(_horses([0.1, None, 0.3, None]))
        assert sorted(h.horse_number for h in got) == [1, 2, 3, 4]

    def test_unrated_go_last(self) -> None:
        got = sort_by_confidence(_horses([None, 0.01, None, 0.5]))
        assert [h.horse_number for h in got] == [4, 2, 1, 3]

    def test_ties_break_by_horse_number(self) -> None:
        """同値でも並びが実行ごとに変わらないこと。"""
        got = sort_by_confidence(_horses([0.2, 0.2, 0.2]))
        assert [h.horse_number for h in got] == [1, 2, 3]

    def test_missing_horse_number_goes_last(self) -> None:
        horses = [BoardHorse(None, "未確定", None, 0.2), BoardHorse(3, "ウマ3", None, 0.2)]
        assert [h.horse_number for h in sort_by_confidence(horses)] == [3, None]

    def test_empty(self) -> None:
        assert sort_by_confidence([]) == []


class TestRank:
    def test_sequential_from_one(self) -> None:
        ordered = sort_by_confidence(_horses([0.5, 0.3, 0.1]))
        assert rank_in_race(ordered) == [1, 2, 3]

    def test_unrated_has_no_rank(self) -> None:
        """未算出の馬に順位を振ると、実在しない順位が生まれる。"""
        ordered = sort_by_confidence(_horses([0.5, None, 0.1]))
        assert rank_in_race(ordered) == [1, 2, None]

    def test_empty(self) -> None:
        assert rank_in_race([]) == []
