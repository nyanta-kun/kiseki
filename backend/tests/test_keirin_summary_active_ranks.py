"""サマリーから「直近で売っていないプラン」を外す仕組みの回帰テスト（2026-09-15）。

ユーザー要望「サマリーに大量のモデルを表示していますが、無効にしたモデルに
ついては表示、集計から通常表示では外してください」。「無効」の定義は
**直近で売っていないもの**（入稿設定は見ない）。

守るのは4点:

1. 窓は基準日を終点とする直近14日。**商品差し替え日（2026-09-15）より前は数えない**
   （基準日が差し替え日より前なら差し替えは考慮しない）
2. **fail-open**: 窓内の実売が1件も無ければ絞らない
3. 既定の合計（件数・的中・投資・回収・最大払戻・候補数）から非アクティブを外し、
   **全部込みの合計も同じ行から作って返す**（トグルのたびに取り直さない）
4. ランク別（`by_rank`）は全プランのまま返す（絞るのはフロント）
"""
from __future__ import annotations

import inspect
from datetime import date
from typing import Any

import pytest

from src.api import keirin_router as R
from src.services.keirin_active_ranks import (
    ACTIVE_WINDOW_DAYS,
    PRODUCT_SWITCH_DATE,
    active_window,
    inactive_labels,
    is_active,
    resolve_active,
)

# ---------------------------------------------------------------------------
# 純関数
# ---------------------------------------------------------------------------


def test_switch_date_and_window_constants():
    assert PRODUCT_SWITCH_DATE == date(2026, 9, 15)
    assert ACTIVE_WINDOW_DAYS == 14


def test_window_starts_at_switch_date_right_after_switch():
    """差し替え当日〜13日後は、差し替え日より前を数えない。"""
    assert active_window(date(2026, 9, 15)) == (date(2026, 9, 15), date(2026, 9, 15))
    assert active_window(date(2026, 9, 20)) == (date(2026, 9, 15), date(2026, 9, 20))
    assert active_window(date(2026, 9, 28)) == (date(2026, 9, 15), date(2026, 9, 28))


def test_window_is_14_days_once_switch_is_old_enough():
    assert active_window(date(2026, 9, 29)) == (date(2026, 9, 16), date(2026, 9, 29))
    assert active_window(date(2026, 12, 31)) == (date(2026, 12, 18), date(2026, 12, 31))


def test_window_ignores_switch_before_switch_date():
    """過去日（差し替え前）を見るときは差し替えを考慮せず 14日窓。"""
    assert active_window(date(2026, 9, 14)) == (date(2026, 9, 1), date(2026, 9, 14))
    assert active_window(date(2026, 8, 10)) == (date(2026, 7, 28), date(2026, 8, 10))


def test_resolve_active_is_fail_open():
    assert resolve_active([]) is None
    assert resolve_active(["T_firm", "T_firm", "A_ana"]) == frozenset({"T_firm", "A_ana"})


def test_is_active_strips_car_count_and_passes_everything_when_unfiltered():
    active = frozenset({"A_hit"})
    assert is_active("A_hit", active)
    assert is_active("A_hit@9", active)
    assert not is_active("B_hit", active)
    assert not is_active("B_hit@7", active)
    assert is_active("B_hit", None)


def test_inactive_labels_keeps_order_and_is_empty_when_unfiltered():
    order = ["T_firm", "T_mid", "A_hit", "B_hit", "A_hit"]
    assert inactive_labels(order, frozenset({"T_firm", "A_hit"})) == ["T_mid", "B_hit"]
    assert inactive_labels(order, None) == []


def test_sum_paper_filters_by_label():
    by_label = {
        "7S": {"n_picks": 2, "n_hits": 1, "total_bet": 200, "total_payout": 500,
               "max_payout": 500},
        "7C": {"n_picks": 3, "n_hits": 0, "total_bet": 300, "total_payout": 0,
               "max_payout": 0},
    }
    assert R._sum_paper(by_label, None) == {
        "n_picks": 5, "n_hits": 1, "total_bet": 500, "total_payout": 500,
        "max_payout": 500}
    assert R._sum_paper(by_label, frozenset({"7C"}))["n_picks"] == 3
    assert R._sum_paper(by_label, frozenset({"T_firm"}))["n_picks"] == 0
    assert R._sum_paper({}, None)["n_picks"] == 0


# ---------------------------------------------------------------------------
# DB を偽物にして集計の振る舞いを見る
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows: list[Any]):
        self._rows = rows

    def mappings(self) -> _Result:
        return self

    def scalars(self) -> _Result:
        return self

    def all(self) -> list[Any]:
        return self._rows


class _FakeDB:
    def __init__(self, rows: list[Any]):
        self.rows = rows
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, stmt: Any, params: dict | None = None) -> _Result:
        self.calls.append((str(stmt), dict(params or {})))
        return _Result(self.rows)


def _sold(rank_key: str, bet: int, payout: int, cars: int = 7) -> dict[str, Any]:
    return {"race_key": f"r_{rank_key}_{bet}", "rank_key": rank_key, "origin": "auto",
            "race_date": "2026-09-15", "n_entries": cars,
            "bet": bet, "payout": payout, "hit": payout > 0}


@pytest.fixture
def sold_rows(monkeypatch):
    rows = [
        _sold("T_firm", 1000, 3000),
        _sold("T_firm", 1000, 0),
        _sold("A_hit", 1000, 50000, cars=9),   # 直近で売っていない側
    ]

    async def fake_fetch(*_a, **_k):
        return rows, 2

    monkeypatch.setattr(R, "_fetch_settled_submissions", fake_fetch)
    return rows


def _cand_rows(total: int, active_total: int) -> list[dict[str, Any]]:
    return [
        {"rank": "RANK_7S", "is_active": None, "g_rank": 0, "g_active": 1, "n_candidates": 4},
        {"rank": None, "is_active": True, "g_rank": 1, "g_active": 0,
         "n_candidates": active_total},
        {"rank": None, "is_active": False, "g_rank": 1, "g_active": 0, "n_candidates": 9},
        {"rank": None, "is_active": None, "g_rank": 1, "g_active": 1, "n_candidates": total},
    ]


async def test_aggregate_excludes_inactive_from_default_totals(sold_rows):
    db = _FakeDB(_cand_rows(total=10, active_total=1))
    period, all_totals = await R._aggregate(
        db, "ph.race_date = :d", {"d": "2026-09-15"},
        from_dt=date(2026, 9, 15), to_dt=date(2026, 9, 15),
        active=frozenset({"T_firm"}))

    # 既定: T_firm だけ
    assert (period["n_picks"], period["n_hits"]) == (2, 1)
    assert (period["total_bet"], period["total_payout"]) == (2000, 3000)
    assert period["max_payout"] == 3000
    assert period["n_candidates"] == 1
    assert period["n_unpriced"] == 2
    # 全部込み
    assert (all_totals["n_picks"], all_totals["n_hits"]) == (3, 2)
    assert all_totals["total_payout"] == 53000
    assert all_totals["max_payout"] == 50000
    assert all_totals["n_candidates"] == 10
    assert "by_rank" not in all_totals
    # ランク別は全プランのまま（絞るのはフロント）
    assert {"T_firm", "T_firm@7", "A_hit", "A_hit@9", "7S"} <= set(period["by_rank"])
    # 候補数のスキャンは1本で、アクティブな内部名を渡している
    assert len(db.calls) == 1
    sql, params = db.calls[0]
    assert "GROUPING SETS ((c.rank), (c.is_active), ())" in sql
    assert "ANY(:active_ranks)" in sql
    assert params["active_ranks"] == []   # T_firm は picks_history に行を持たない


async def test_aggregate_unfiltered_totals_match(sold_rows):
    """fail-open（active=None）なら既定と全部込みが一致する。"""
    db = _FakeDB([
        {"rank": None, "is_active": True, "g_rank": 1, "g_active": 0, "n_candidates": 10},
        {"rank": None, "is_active": None, "g_rank": 1, "g_active": 1, "n_candidates": 10},
    ])
    period, all_totals = await R._aggregate(
        db, "ph.race_date = :d", {"d": "2026-09-15"},
        from_dt=date(2026, 9, 15), to_dt=date(2026, 9, 15), active=None)
    for k in ("n_picks", "n_hits", "total_bet", "total_payout", "max_payout",
              "n_candidates"):
        assert period[k] == all_totals[k], k
    sql, params = db.calls[0]
    assert "ANY(:active_ranks)" not in sql and "active_ranks" not in params


async def test_active_rank_labels_uses_window_and_excludes_cancelled():
    db = _FakeDB(["T_firm", "A_ana", "7A"])
    got = await R._active_rank_labels(db, date(2026, 9, 20))
    assert got == frozenset({"T_firm", "A_ana", "7A"})
    sql, params = db.calls[0]
    assert params == {"from_date": "2026-09-15", "to_date": "2026-09-20"}
    assert "ns.deleted_at IS NULL" in sql


async def test_active_rank_labels_fail_open_when_nothing_sold():
    assert await R._active_rank_labels(_FakeDB([]), date(2026, 9, 15)) is None


# ---------------------------------------------------------------------------
# get_summary の配線
# ---------------------------------------------------------------------------

SUMMARY_SRC = inspect.getsource(R.get_summary)


def test_summary_decides_active_once_before_gather():
    """🔴 3期間で同じ集合を使う（期間ごとに判定すると当日と当年で食い違う）。"""
    assert SUMMARY_SRC.count("_active_rank_labels(") == 1
    assert SUMMARY_SRC.index("_active_rank_labels(") < SUMMARY_SRC.index("asyncio.gather")
    assert "active=active" in SUMMARY_SRC


def test_summary_returns_both_variants_and_inactive_ranks():
    for key in ('"all"', '"inactive_ranks"', '"active_window"'):
        assert key in SUMMARY_SRC, key
    # ペーパーも両方にそれぞれ足す
    assert "_sum_paper(paper, active)" in SUMMARY_SRC
    assert "_sum_paper(paper, None)" in SUMMARY_SRC
