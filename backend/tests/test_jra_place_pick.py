"""複勝ピック（`services/jra_place_pick.py`）の判定と、当月一覧の配線を固定する。"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.services.jra_place_pick import (
    MIN_FIELD,
    PlacePickHorse,
    place_probability_ranks,
    place_slots,
    select_place_pick,
)
from src.services.jra_place_pick_month import LiveResult, _status, assemble_place_pick_month

ROOT = Path(__file__).resolve().parents[1]


def _field(n: int = 10) -> list[PlacePickHorse]:
    """条件に当たる馬のいないフィールド（複勝確率は馬番が小さいほど高い）。"""
    return [
        PlacePickHorse(horse_number=i, win_odds=2.0 + i, place_odds=1.2 + i * 0.1, place_probability=0.60 - i * 0.04)
        for i in range(1, n + 1)
    ]


def _with(horses: list[PlacePickHorse], hn: int, **kw) -> list[PlacePickHorse]:
    return [PlacePickHorse(**{**h.__dict__, **kw}) if h.horse_number == hn else h for h in horses]


def test_no_candidate_returns_none() -> None:
    assert select_place_pick(_field()) is None


def test_basic_pick() -> None:
    horses = _with(_field(), 3, win_odds=10.0, place_odds=3.2)  # 比 3.125・複勝確率3位
    assert select_place_pick(horses) == 3


@pytest.mark.parametrize(
    ("place_odds", "expected"),
    [(2.9, None), (3.0, 3), (3.9, 3), (4.0, None)],
)
def test_place_odds_band(place_odds: float, expected: int | None) -> None:
    horses = _with(_field(), 3, win_odds=place_odds * 3.0, place_odds=place_odds)
    assert select_place_pick(horses) == expected


@pytest.mark.parametrize(("win_odds", "expected"), [(11.2, 3), (11.3, None)])
def test_win_place_ratio_inclusive(win_odds: float, expected: int | None) -> None:
    # 11.2 / 3.2 = 3.5（境界は含む）
    horses = _with(_field(), 3, win_odds=win_odds, place_odds=3.2)
    assert select_place_pick(horses) == expected


def test_place_probability_rank_limit() -> None:
    assert select_place_pick(_with(_field(), 5, win_odds=10.0, place_odds=3.2)) == 5
    assert select_place_pick(_with(_field(), 6, win_odds=10.0, place_odds=3.2)) is None


def test_small_field_is_skipped() -> None:
    horses = _with(_field(MIN_FIELD - 1), 3, win_odds=10.0, place_odds=3.2)
    assert select_place_pick(horses) is None
    horses = _with(_field(MIN_FIELD), 3, win_odds=10.0, place_odds=3.2)
    assert select_place_pick(horses) == 3


def test_horse_without_win_odds_is_not_in_field() -> None:
    # 単勝オッズの無い馬はフィールドにも順位にも数えない
    horses = _with(_field(8), 8, win_odds=None)
    horses = _with(horses, 3, win_odds=10.0, place_odds=3.2)
    assert select_place_pick(horses) is None  # 7頭立て扱い
    assert 8 not in place_probability_ranks(horses)


def test_multiple_candidates_pick_highest_place_probability() -> None:
    horses = _with(_field(), 2, win_odds=10.0, place_odds=3.2)
    horses = _with(horses, 4, win_odds=10.0, place_odds=3.2)
    assert select_place_pick(horses) == 2


def test_place_slots() -> None:
    assert (place_slots(8), place_slots(7), place_slots(5), place_slots(4)) == (3, 2, 2, 0)


@pytest.mark.parametrize(
    ("abn", "fp", "pay", "finished", "expected"),
    [
        (None, None, None, False, "pending"),  # 発走前・結果待ち
        (0, 3, 3.4, True, "hit"),
        (0, 3, None, True, "pending"),  # 3着内・払戻未着（HR 待ち）
        (0, 4, None, True, "miss"),  # 着順だけで外れが決まる
        (1, None, None, True, "void"),  # 取消 = 返還
        (0, 0, None, True, "miss"),  # 競走中止など
        (None, None, None, True, "miss"),  # 結果は出たがこの馬の行が無い
    ],
)
def test_month_status(abn, fp, pay, finished, expected) -> None:
    assert _status(abn, fp, pay, finished, 3) == expected


def _snapshot_race(race_id: int = 1, *, settled: bool) -> tuple[list, list]:
    """8頭立て・3番が条件に当たるスナップショット1レース分。"""
    lr = SimpleNamespace(race_id=race_id, date="20260927", course_name="中山", race_number=10, post_time="1500")
    picks = []
    for h in _with(_field(8), 3, win_odds=10.0, place_odds=3.2):
        picks.append(
            SimpleNamespace(
                race_id=race_id,
                horse_number=h.horse_number,
                horse_name=f"馬{h.horse_number}",
                pre_win_odds=h.win_odds,
                pre_place_odds=h.place_odds,
                place_probability=h.place_probability,
                pop_rank=None,
                abnormality_code=0 if settled else None,
                finish_position=(1 if h.horse_number == 3 else 9) if settled else None,
                place_payout_odds=(2.9 if h.horse_number == 3 else None) if settled else None,
                final_win_odds=9.5 if settled else None,
                settled_at="x" if settled else None,
            )
        )
    return [(lr, "R", "芝", 1800)], picks


def test_results_show_before_nightly_settle() -> None:
    """settle（23:45）前でも race_results に入った着順・払戻が一覧に出る。"""
    races, picks = _snapshot_race(settled=False)
    d = assemble_place_pick_month("202609", races, picks)
    assert d["picks"][0]["status"] == "pending"

    live = {1: {3: LiveResult(2, 0, 3.6, 9.8), 1: LiveResult(1, 0, 1.2, 2.1)}}
    latest = {1: ({3: 9.9}, {3: 3.5})}
    d = assemble_place_pick_month("202609", races, picks, live=live, latest_odds=latest)
    row = d["picks"][0]
    assert (row["status"], row["finish_position"], row["place_payout"]) == ("hit", 2, 360)
    assert row["now_win_odds"] == 9.8  # 確定単勝オッズを優先
    assert row["pre_place_odds"] == 3.2  # 判定に使った値はそのまま
    assert d["summary"]["n_hits"] == 1


def test_settled_rows_ignore_live_overlay() -> None:
    races, picks = _snapshot_race(settled=True)
    live = {1: {3: LiveResult(9, 0, None, 1.0)}}
    row = assemble_place_pick_month("202609", races, picks, live=live)["picks"][0]
    assert (row["status"], row["place_payout"], row["now_win_odds"]) == ("hit", 290, 9.5)


def test_both_screens_use_the_single_rule() -> None:
    """レース詳細（races.py）と当月一覧が同じ判定関数を呼んでいること。

    条件を画面ごとに書き写すと、閾値を変えたときに片方だけ古い条件で残る。
    """
    for rel in ("src/api/races.py", "src/services/jra_place_pick_month.py"):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "select_place_pick" in calls, rel
