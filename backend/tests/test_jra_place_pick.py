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
from src.services.jra_place_pick_month import _status

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
    ("kw", "expected"),
    [
        ({"finish_position": None, "settled_at": None}, "pending"),
        ({"finish_position": 3, "place_payout_odds": 3.4}, "hit"),
        ({"finish_position": 4}, "miss"),
        ({"abnormality_code": 1}, "void"),
        ({"finish_position": 0, "settled_at": "x"}, "miss"),  # 競走中止など
    ],
)
def test_month_status(kw: dict, expected: str) -> None:
    base = {"abnormality_code": 0, "finish_position": None, "settled_at": "x", "place_payout_odds": None}
    assert _status(SimpleNamespace(**{**base, **kw}), 3) == expected


def test_both_screens_use_the_single_rule() -> None:
    """レース詳細（races.py）と当月一覧が同じ判定関数を呼んでいること。

    条件を画面ごとに書き写すと、閾値を変えたときに片方だけ古い条件で残る。
    """
    for rel in ("src/api/races.py", "src/services/jra_place_pick_month.py"):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "select_place_pick" in calls, rel
