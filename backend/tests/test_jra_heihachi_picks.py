"""平八ピック一覧（推奨ページ）の純粋ロジックのテスト [[jra_heihachi_badge]]"""

from __future__ import annotations

from typing import Any

from src.services.jra_heihachi_picks import CANDIDATE_INDEX_RANK_MAX, select_candidates


def _entry(
    horse_number: int,
    composite_index: float | None,
    win_odds: float | None,
    place_probability: float | None,
    grade: str | None = "OP特別",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "race_id": 1,
        "course_name": "中山",
        "race_number": 11,
        "race_name": None,
        "post_time": "1545",
        "grade": grade,
        "horse_number": horse_number,
        "horse_name": f"ウマ{horse_number}",
        "composite_index": composite_index,
        "place_probability": place_probability,
        "win_odds": win_odds,
        "finish_position": None,
        "result_place_odds": None,
        "result_win_odds": None,
        "abnormality_code": 0,
        **extra,
    }


def test_select_candidates_ranks_by_composite() -> None:
    """composite 降順に index_rank を振る（オッズ・複勝率では絞らない）。"""
    entries = [
        _entry(1, 60.0, 20.0, 0.20),
        _entry(2, 80.0, 20.0, 0.45),
        _entry(3, 70.0, 2.0, 0.60),
    ]
    got = select_candidates(entries)
    assert [(c["horse_number"], c["index_rank"]) for c in got] == [(2, 1), (3, 2), (1, 3)]


def test_select_candidates_caps_at_rank_max() -> None:
    entries = [_entry(i, 100.0 - i, 10.0, 0.4) for i in range(1, 10)]
    got = select_candidates(entries)
    assert len(got) == CANDIDATE_INDEX_RANK_MAX
    assert got[-1]["index_rank"] == CANDIDATE_INDEX_RANK_MAX


def test_select_candidates_keeps_all_grades() -> None:
    """grade による絞り込みはフロント側の責務なので、ここでは落とさない。"""
    entries = [_entry(1, 80.0, 20.0, 0.45, grade=None)]
    assert len(select_candidates(entries)) == 1


def test_select_candidates_skips_missing_index() -> None:
    entries = [_entry(1, None, 20.0, 0.45), _entry(2, 80.0, 20.0, 0.45)]
    assert [c["horse_number"] for c in select_candidates(entries)] == [2]


def test_select_candidates_ignores_scratched() -> None:
    """取消馬は母集団から外すので、後続の指数順位が1つ繰り上がる。"""
    full = [
        _entry(1, 90.0, 3.0, 0.60),
        _entry(2, 85.0, 4.0, 0.55),
        _entry(3, 80.0, 5.0, 0.50),
        _entry(4, 40.0, 20.0, 0.45),
    ]
    assert [c["index_rank"] for c in select_candidates(full)] == [1, 2, 3, 4]

    scratched = [{**full[0], "abnormality_code": 1}, *full[1:]]
    got = select_candidates(scratched)
    assert [c["horse_number"] for c in got] == [2, 3, 4]
    assert got[-1]["index_rank"] == 3  # 1頭抜けて繰り上がった


def test_select_candidates_empty() -> None:
    assert select_candidates([]) == []


def test_defaults_are_pinned_to_frontend_fallback() -> None:
    """既定値はフロントの HEIHACHI_FALLBACK_DEFAULTS と一致していること。

    レース詳細のバッジは（保存値がないとき）フロント側の定数で判定するため、
    ここがずれると「推奨ページとレース詳細で対象馬が違う」事故になる。
    値を変えるときは frontend/src/lib/heihachi.ts も必ず一緒に直すこと。
    """
    from src.indices.dm_signals import (
        HEIHACHI_COMP_RANK_MAX,
        HEIHACHI_GRADES,
        HEIHACHI_MAX_ODDS,
        HEIHACHI_MIN_ODDS,
        HEIHACHI_MIN_PLACE_PROB,
    )

    assert HEIHACHI_COMP_RANK_MAX == 3
    assert HEIHACHI_MIN_ODDS == 15.0
    assert HEIHACHI_MAX_ODDS == 40.0
    assert HEIHACHI_MIN_PLACE_PROB == 0.40
    assert HEIHACHI_GRADES == frozenset({"OP特別", "Listed", "G3", "G2", "G1", "重賞"})


# ---------------------------------------------------------------------------
# 年間バックテスト集計（aggregate_backtest）
# 行は (graded, index_rank, win_odds, place_odds, place_prob, is_win)
# ---------------------------------------------------------------------------

_DEFAULT_KW = {
    "max_index_rank": 3,
    "min_odds": 15.0,
    "max_odds": 40.0,
    "min_place_prob": 0.40,
    "graded_only": True,
}


def test_aggregate_backtest_empty() -> None:
    from src.services.jra_heihachi_picks import aggregate_backtest

    got = aggregate_backtest([], **_DEFAULT_KW)  # type: ignore[arg-type]
    assert got["n"] == 0
    assert got["win_roi"] is None and got["place_roi"] is None


def test_aggregate_backtest_counts_and_roi() -> None:
    from src.services.jra_heihachi_picks import aggregate_backtest

    rows = [
        (True, 1, 20.0, 4.0, 0.40, True),    # 該当・1着
        (True, 2, 16.0, 3.0, 0.45, False),   # 該当・3着内（複勝のみ）
        (True, 3, 17.0, None, 0.41, False),  # 該当・着外
        (True, 1, 5.0, 2.0, 0.50, True),     # オッズ下限未満で除外
        (False, 1, 20.0, 4.0, 0.40, True),   # 平場なので除外
    ]
    got = aggregate_backtest(rows, **_DEFAULT_KW)  # type: ignore[arg-type]
    assert got["n"] == 3
    assert got["win_hits"] == 1 and got["place_hits"] == 2
    assert got["win_roi"] == 20.0 / 3
    assert got["place_roi"] == 7.0 / 3


def test_aggregate_backtest_odds_band_matches_frontend() -> None:
    """オッズは下限を含み上限を含まない（frontend matchesHeihachi と同じ）。"""
    from src.services.jra_heihachi_picks import aggregate_backtest

    for odds, expected in [(14.99, 0), (15.0, 1), (39.99, 1), (40.0, 0)]:
        rows = [(True, 1, odds, None, 0.40, False)]
        got = aggregate_backtest(rows, **_DEFAULT_KW)  # type: ignore[arg-type]
        assert got["n"] == expected, odds


def test_aggregate_backtest_graded_only_toggle() -> None:
    from src.services.jra_heihachi_picks import aggregate_backtest

    rows = [(False, 1, 20.0, 4.0, 0.40, True)]
    assert aggregate_backtest(rows, **_DEFAULT_KW)["n"] == 0  # type: ignore[arg-type]
    assert aggregate_backtest(rows, **{**_DEFAULT_KW, "graded_only": False})["n"] == 1  # type: ignore[arg-type]


def test_aggregate_backtest_rank_and_place_prob_floors() -> None:
    from src.services.jra_heihachi_picks import aggregate_backtest

    assert aggregate_backtest(
        [(True, 4, 20.0, None, 0.40, False)], **_DEFAULT_KW  # type: ignore[arg-type]
    )["n"] == 0
    assert aggregate_backtest(
        [(True, 1, 20.0, None, 0.39, False)], **_DEFAULT_KW  # type: ignore[arg-type]
    )["n"] == 0
