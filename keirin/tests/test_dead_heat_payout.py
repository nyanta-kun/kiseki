"""同着で当たり目が2通りあるとき、買っていれば**両方**払い戻す（2026-09-21）。

## 何を守るか

`result_top3.hit_trio` / `hit_trifecta` は**買った目のうち当たったものを1つ**しか
返さず、`submitted_stakes.resolve_payout` も `winning_key` を単数で受ける。
そのため同着で2通りとも買っていたレースで**片方しか払戻が記録されていなかった**。

実測（2026-09-21）: `picks_history` **13行**が過少記録。確定事例
`20260822_31_03#9C`（3着が7番と9番の同着・`3=5=7 ¥5,400` と `3=5=9 ¥2,500` を
両方入稿）は記録 **¥12,420 ↔ 正しくは ¥25,419（−¥13,000）**。

🔴 **直したのは共通入口（`resolve_payout`）の中だけ**で、`notify_results_wt.py` の
   12箇所・backfill 14本の呼び出し側は1つも変えていない。
   **同着でないレースでは返り値が 1円も変わらないこと**をここで固定する。
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.submitted_stakes import (  # noqa: E402
    dead_heat_extra_payout, payout_per_100, resolve_payout,
)

RK = "20260822_31_03"


def _conn(finishers, odds):
    """TOP3_SQL と wt_odds の2種類だけ答える最小の偽 conn。"""
    class _Cur:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

        def fetchone(self):
            return self._rows[0] if self._rows else None

    class _Conn:
        def execute(self, sql, params=()):
            if "finish_order BETWEEN 1 AND 3" in sql:
                return _Cur(finishers)
            if "FROM wt_odds" in sql:
                _, bet_type = params
                return _Cur([(combo, v) for (bt, combo), v in odds.items()
                             if bt == bet_type])
            raise AssertionError(f"想定外のSQL: {sql}")

    return _Conn()


def _bet_detail(lines, total):
    return {"total": total, "lines": lines}


@contextmanager
def _submitted(lines, total):
    with patch("src.submitted_stakes.submitted_stakes",
               lambda conn, rk, rank: ({
                   __import__("src.submitted_stakes", fromlist=["_combo_key"])
                   ._combo_key(bt, combo): stake for bt, combo, stake in lines}, total)):
        yield


# ───────────────────────────────────────────────────────────── 同着でないとき

def test_no_dead_heat_changes_nothing():
    """同着でなければ追加は 0 円（既存の数字をビット単位で変えない）。"""
    conn = _conn([(1, 3), (2, 5), (3, 7)], {})
    stakes = {frozenset({3, 5, 7}): 1000}
    assert dead_heat_extra_payout(conn, RK, stakes, frozenset({3, 5, 7})) == 0


def test_dead_heat_but_only_one_side_bought():
    """同着でも買っていない方は払われない。"""
    conn = _conn([(1, 3), (2, 5), (3, 7), (3, 9)],
                 {("trio", "3=5=9"): 25.0})
    stakes = {frozenset({3, 5, 7}): 1000}
    assert dead_heat_extra_payout(conn, RK, stakes, frozenset({3, 5, 7})) == 0


# ───────────────────────────────────────────────────────────── 同着で両方買ったとき

def test_dead_heat_both_bought_is_summed():
    """🔴 本体。2通りとも買っていれば両方払い戻される。"""
    conn = _conn([(1, 3), (2, 5), (3, 7), (3, 9)],
                 {("trio", "3=5=7"): 54.0, ("trio", "3=5=9"): 25.0})
    stakes = {frozenset({3, 5, 7}): 2300, frozenset({3, 5, 9}): 5200}
    extra = dead_heat_extra_payout(conn, RK, stakes, frozenset({3, 5, 7}))
    assert extra == 2500 * 5200 // 100          # 3=5=9（25.0倍）のぶん
    # すでに払った側は二重に数えず、反対側を足す
    assert dead_heat_extra_payout(conn, RK, stakes, frozenset({3, 5, 9})) == \
        5400 * 2300 // 100


def test_trifecta_dead_heat():
    """三連単でも同じ（2着同着なら順序違いの2通り）。"""
    conn = _conn([(1, 3), (2, 5), (2, 7)],
                 {("trifecta", "3-5-7"): 120.0, ("trifecta", "3-7-5"): 90.0})
    stakes = {(3, 5, 7): 1000, (3, 7, 5): 2000}
    assert dead_heat_extra_payout(conn, RK, stakes, (3, 5, 7)) == 9000 * 2000 // 100


def test_missing_odds_is_logged_not_silently_zero(caplog):
    """もう一方の払戻が引けないときは黙って 0 にせず警告を残す。"""
    conn = _conn([(1, 3), (2, 5), (3, 7), (3, 9)], {("trio", "3=5=7"): 54.0})
    stakes = {frozenset({3, 5, 7}): 1000, frozenset({3, 5, 9}): 1000}
    with caplog.at_level("WARNING"):
        assert dead_heat_extra_payout(conn, RK, stakes, frozenset({3, 5, 7})) == 0
    assert "同着" in caplog.text


# ───────────────────────────────────────────────────────────── 共通入口ごしの確認

def test_resolve_payout_includes_the_other_winning_leg():
    """呼び出し側を変えずに `resolve_payout` の返り値が直ること。"""
    conn = _conn([(1, 3), (2, 5), (3, 7), (3, 9)],
                 {("trio", "3=5=7"): 54.0, ("trio", "3=5=9"): 25.0})
    lines = [("trio", "3=5=7", 2300), ("trio", "3=5=9", 5200)]
    with _submitted(lines, 7500):
        pay, bet = resolve_payout(
            conn, RK, "9C", hit=True, winning_key=frozenset({3, 5, 7}),
            odds_payout=5400, fallback_stake=1500, n_combos=2)
    assert bet == 7500
    assert pay == 5400 * 2300 // 100 + 2500 * 5200 // 100


def test_resolve_payout_is_unchanged_without_dead_heat():
    conn = _conn([(1, 3), (2, 5), (3, 7)], {})
    lines = [("trio", "3=5=7", 2300), ("trio", "3=5=9", 5200)]
    with _submitted(lines, 7500):
        pay, bet = resolve_payout(
            conn, RK, "9C", hit=True, winning_key=frozenset({3, 5, 7}),
            odds_payout=5400, fallback_stake=1500, n_combos=2)
    assert (pay, bet) == (5400 * 2300 // 100, 7500)


# ───────────────────────────────────────────────────────────── 払戻換算の一致

def test_payout_per_100_matches_the_backtest_formula():
    """🔴 `backtest_wt._load_payouts_wt` と同じ式であることを固定する。"""
    for odds in (1.0, 2.5, 12.3, 54.0, 999.9, 1234.56):
        assert payout_per_100(odds) == round(odds * 100) // 10 * 10
    assert payout_per_100(None) == 0


def test_trio_combination_is_hyphen_separated_in_wt_odds():
    """🔴 `wt_odds.combination` は**三連複も `-` 区切り**（実測）。

    `=` 決め打ちで引くと1件も見つからないのに例外は出ず、**静かに 0 円**になる。
    実装は書式を仮定せずパースすること。
    """
    conn = _conn([(1, 5), (2, 3), (3, 7), (3, 9)],
                 {("trio", "3-5-7"): 2.3, ("trio", "3-5-9"): 5.2})
    stakes = {frozenset({3, 5, 7}): 5400, frozenset({3, 5, 9}): 2500}
    assert dead_heat_extra_payout(conn, "20260822_31_03", stakes,
                                  frozenset({3, 5, 7})) == 520 * 2500 // 100


def test_real_case_20260822_31_03():
    """実データ1件の答え合わせ（本番 DB から写した値）。

        着順 1着=5 / 2着=3 / **3着=7 と 9 の同着**
        入稿 3=5=7 ¥5,400 ・ 3=5=9 ¥2,500（ほか3点）
        確定 trio 3-5-7 = 2.3倍 / 3-5-9 = 5.2倍

        記録されていた払戻 ¥12,420（= 230 × 5,400 ÷ 100・**片方だけ**）
        正しい払戻       ¥25,420（+ 520 × 2,500 ÷ 100 = ¥13,000）
    """
    conn = _conn([(1, 5), (2, 3), (3, 7), (3, 9)],
                 {("trio", "3-5-7"): 2.3, ("trio", "3-5-9"): 5.2})
    lines = [("trio", "3=5=7", 5400), ("trio", "3=5=9", 2500),
             ("trio", "1=3=5", 1100)]
    with _submitted(lines, 10000):
        pay, bet = resolve_payout(
            conn, "20260822_31_03", "9C", hit=True,
            winning_key=frozenset({3, 5, 7}), odds_payout=230,
            fallback_stake=2000, n_combos=3)
    assert bet == 10000
    assert pay == 25420          # 記録は 12420 だった
