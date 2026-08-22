"""レース信頼度一覧（推奨ページ）の行畳み込み ユニットテスト

`summarize_race` の純粋関数テスト。DBアクセスなし。
"""

from __future__ import annotations

from src.services.jra_race_confidence import summarize_race

# ---------------------------------------------------------------------------
# ヘルパ
# ---------------------------------------------------------------------------


def _entry(
    horse_number: int,
    *,
    composite_index: float | None = 50.0,
    win_probability: float | None = 0.1,
    win_odds: float | None = 5.0,
    horse_name: str | None = None,
    head_count: int | None = None,
    finish_position: int | None = None,
) -> dict:
    """出走馬1頭ぶんの行を作る。レース属性は全頭で同じ値を持つ想定。"""
    return {
        "race_id": 1234,
        "course_name": "新潟",
        "race_number": 6,
        "race_name": "3歳未勝利",
        "post_time": "1025",
        "surface": "芝",
        "distance": 1800,
        "head_count": head_count,
        "horse_number": horse_number,
        "horse_name": horse_name or f"ウマ{horse_number}",
        "composite_index": composite_index,
        "win_probability": win_probability,
        "win_odds": win_odds,
        "finish_position": finish_position,
    }


# ---------------------------------------------------------------------------
# 表示馬の選択
# ---------------------------------------------------------------------------


class TestFavoriteSelection:
    """表示する馬は「単勝オッズ最小 = 市場1番人気」であること。"""

    def test_picks_lowest_win_odds(self) -> None:
        entries = [
            _entry(1, win_odds=5.0, win_probability=0.10),
            _entry(2, win_odds=1.8, win_probability=0.42, horse_name="ホンメイ"),
            _entry(3, win_odds=9.9, win_probability=0.05),
        ]
        row = summarize_race(entries)
        assert row["horse_number"] == 2
        assert row["horse_name"] == "ホンメイ"
        assert row["win_odds"] == 1.8
        assert row["win_probability"] == 0.42

    def test_favorite_is_market_not_model_top(self) -> None:
        """指数1位と市場1番人気が別馬でも、表示されるのは市場1番人気。"""
        entries = [
            # 指数トップだが人気は2番手
            _entry(1, composite_index=80.0, win_odds=3.0, win_probability=0.35),
            # 単勝最小 = 市場1番人気
            _entry(2, composite_index=40.0, win_odds=2.0, win_probability=0.20),
        ]
        row = summarize_race(entries)
        assert row["horse_number"] == 2

    def test_ev_is_odds_times_probability(self) -> None:
        entries = [
            _entry(1, win_odds=2.5, win_probability=0.40),
            _entry(2, win_odds=8.0, win_probability=0.10),
        ]
        row = summarize_race(entries)
        assert row["ev"] == 1.0  # 2.5 * 0.40

    def test_ev_keeps_full_precision(self) -> None:
        """表示は小数第1位だが、並び替えが潰れないよう素の値を返すこと。"""
        entries = [_entry(1, win_odds=1.4, win_probability=0.335)]
        row = summarize_race(entries)
        assert row["ev"] is not None
        assert abs(row["ev"] - 0.469) < 1e-9


# ---------------------------------------------------------------------------
# 欠損データ（行は必ず返す）
# ---------------------------------------------------------------------------


class TestMissingData:
    """指数やオッズが欠けても行自体は返し、欠損項目だけ None にすること。"""

    def test_no_odds_keeps_row(self) -> None:
        entries = [
            _entry(1, win_odds=None),
            _entry(2, win_odds=None),
        ]
        row = summarize_race(entries)
        assert row["race_id"] == 1234
        assert row["horse_number"] is None
        assert row["win_odds"] is None
        assert row["ev"] is None
        # 指数はあるので信頼度スコアは出る
        assert row["confidence_score"] is not None

    def test_no_indices_keeps_row(self) -> None:
        entries = [
            _entry(1, composite_index=None, win_probability=None, win_odds=3.0),
            _entry(2, composite_index=None, win_probability=None, win_odds=6.0),
        ]
        row = summarize_race(entries)
        assert row["confidence_score"] is None
        assert row["tier"] is None
        # オッズはあるので市場1番人気は出る
        assert row["horse_number"] == 1
        assert row["win_odds"] == 3.0
        # 勝率が無ければ EV も出せない
        assert row["ev"] is None

    def test_partial_win_probability_does_not_raise(self) -> None:
        """勝率が一部欠損でも例外にせず、勝率集中スコアだけスキップすること。

        `calculate_race_confidence` は win_probabilities を sorted() するため、
        None が混ざると TypeError になる。呼び出し側で弾いている。
        """
        entries = [
            _entry(1, composite_index=60.0, win_probability=0.3, win_odds=2.0),
            _entry(2, composite_index=50.0, win_probability=None, win_odds=4.0),
        ]
        row = summarize_race(entries)
        assert row["confidence_score"] is not None

    def test_ev_none_when_favorite_has_no_probability(self) -> None:
        entries = [
            _entry(1, win_odds=2.0, win_probability=None),
            _entry(2, win_odds=5.0, win_probability=0.2),
        ]
        row = summarize_race(entries)
        assert row["horse_number"] == 1
        assert row["win_probability"] is None
        assert row["ev"] is None


# ---------------------------------------------------------------------------
# レース属性
# ---------------------------------------------------------------------------


class TestRaceAttributes:
    def test_head_count_falls_back_to_entry_count(self) -> None:
        """races.head_count は確定成績から埋まる列で発走前は NULL。出走頭数で代替する。"""
        entries = [_entry(i, head_count=None) for i in range(1, 13)]
        row = summarize_race(entries)
        assert row["head_count"] == 12

    def test_head_count_uses_column_when_present(self) -> None:
        entries = [_entry(i, head_count=16) for i in range(1, 13)]
        row = summarize_race(entries)
        assert row["head_count"] == 16

    def test_race_attributes_passed_through(self) -> None:
        row = summarize_race([_entry(1)])
        assert row["course_name"] == "新潟"
        assert row["race_number"] == 6
        assert row["post_time"] == "1025"
        assert row["surface"] == "芝"
        assert row["distance"] == 1800

    def test_finish_position_of_favorite(self) -> None:
        entries = [
            _entry(1, win_odds=2.0, finish_position=3),
            _entry(2, win_odds=9.0, finish_position=1),
        ]
        row = summarize_race(entries)
        assert row["finish_position"] == 3  # 1番人気馬の着順


# ---------------------------------------------------------------------------
# tier
# ---------------------------------------------------------------------------


class TestTier:
    def test_odds_on_favorite_gives_s(self) -> None:
        """指数1位馬が断然人気（単勝 < 1.5）なら tier=S（confidence を問わない）。"""
        entries = [
            _entry(1, composite_index=80.0, win_probability=0.6, win_odds=1.2),
            _entry(2, composite_index=50.0, win_probability=0.2, win_odds=6.0),
            _entry(3, composite_index=45.0, win_probability=0.2, win_odds=8.0),
        ]
        row = summarize_race(entries)
        assert row["tier"] == "S"

    def test_market_divergence_is_not_s_or_a(self) -> None:
        """指数1位が市場1番人気でない（市場乖離）なら C 系に落ちること。"""
        entries = [
            _entry(1, composite_index=80.0, win_probability=0.4, win_odds=6.0),
            _entry(2, composite_index=40.0, win_probability=0.3, win_odds=2.0),
            _entry(3, composite_index=39.0, win_probability=0.3, win_odds=2.1),
        ]
        row = summarize_race(entries)
        assert row["tier"] in {"C", "C+"}

    def test_tier_is_none_without_indices(self) -> None:
        entries = [_entry(1, composite_index=None), _entry(2, composite_index=None)]
        assert summarize_race(entries)["tier"] is None
