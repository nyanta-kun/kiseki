"""7T1 を当日中に採点する経路を固定する（2026-08-16）。

## なぜ要るのか

7T1 は**発走前判定を持たない唯一のランク**で、`prerace_decisions_*.json` に
`{rk}#7T1` が無い。そのため `notify_results_wt.py` の採点ループ
（`picks` / `decisions` を辿る）には最初から入らず、**当日中は誰も
hit/payout を書かない**状態だった。投資額だけは入稿時に
`netkeirin_submit_wt._mark_bought` が書くので、

  投資は当日入る → 払戻は翌朝の再構築まで入らない

という非対称になり、`/keirin` の当日サマリー（`bet_amount > 0` で集計）が
**売った商品の的中を丸ごと落とす**。2026-08-16 の小倉3R
（買い目 `1-7-4` 的中・実払戻 176,500円）が0円として積まれ発覚した。

## ここで固定すること

1. 的中を**着順と買い目の一致**で判定し、実払戻を書き戻すこと
2. 未確定（着順が3つ揃わない）レースを外れとして確定させないこと
3. 的中したのに確定配当が引けないときは**書かない**（次回へ回す）。
   払戻0円で確定させると的中がガミ・不的中として残る
4. `pred_combo` の表記を **7C の三単表記と取り違えない**こと
   （7C は `三単:軸1-軸2-相手,相手,…`、7T1 は1点まるごとを並べる）
5. 賭け金・払戻は入稿の原本（`bet_detail`）を正本にすること
"""
from __future__ import annotations

import pytest

import scripts.notify_results_wt as m

DATE = "2026-08-16"
RK = "20260816_53_03"
STORE = f"{RK}#7T1"


class FakeConn:
    """picks_history と wt_entries の SELECT に答え、UPDATE を溜めるだけの conn。"""

    def __init__(self, rows, finish):
        self._rows = rows          # picks_history の行
        self._finish = finish      # [(車番,), ...] 着順どおり
        self.updates: list[tuple] = []

    def execute(self, sql, params=()):
        flat = " ".join(sql.split())
        if flat.startswith("SELECT race_key, pred_combo, bet_amount"):
            return _Result(self._rows)
        if flat.startswith("SELECT frame_no FROM wt_entries"):
            return _Result(self._finish)
        if flat.startswith("UPDATE picks_history"):
            self.updates.append(tuple(params))
            return _Result([])
        raise AssertionError(f"想定外のSQL: {flat}")


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


@pytest.fixture
def patched(monkeypatch):
    """確定配当と入稿原本を差し替える。既定は「三連単 1-7-4 が 3,530円/100円」。"""
    state = {"payouts": {RK: {("trifecta", (1, 7, 4)): 3530}},
             "submitted": (10000, 176500)}

    monkeypatch.setattr(m, "_load_payouts_wt", lambda keys: state["payouts"])

    def _resolve(conn, race_key, rank_key, *, hit, winning_key,
                 odds_payout, fallback_stake, n_combos):
        assert rank_key == "7T1"
        total, pay_when_hit = state["submitted"]
        return (pay_when_hit if hit else 0), total

    monkeypatch.setattr(m, "resolve_payout", _resolve)
    return state


def _rows(pred_combo="三単:1-7-2,1-7-4", bet=10000):
    return [(STORE, pred_combo, bet)]


def _finish(*frames):
    return [(f,) for f in frames]


# ---------------------------------------------------------------------------
# 買い目の読み取り
# ---------------------------------------------------------------------------

def test_7t1の買い目は1点まるごと並ぶ():
    assert m._parse_7t1_legs("三単:1-7-2,1-7-4") == ["1-7-2", "1-7-4"]
    assert m._parse_7t1_legs("三単:1-2-3") == ["1-2-3"]


def test_7cの三単表記は7t1として読まない():
    """🔴 7C は `三単:軸1-軸2-相手,相手,…` で3列目だけを並べる。
    これを 7T1 の買い目として読むと**別ランクの行を誤って採点する**。"""
    assert m._parse_7t1_legs("三単:1-3-2,4,6,5,7") == []


@pytest.mark.parametrize("bad", [None, "", "1-7-4", "2=7-3,4,5", "三単:"])
def test_読めない表記は空で返す(bad):
    assert m._parse_7t1_legs(bad) == []


# ---------------------------------------------------------------------------
# 採点
# ---------------------------------------------------------------------------

def test_的中したら実払戻を書き戻す(patched):
    conn = FakeConn(_rows(), _finish(1, 7, 4))
    assert m._settle_rank_7t1(conn, DATE) == (1, 1, 0)
    (hit, payout, tf_pay, bet, key) = conn.updates[0]
    assert (hit, payout, tf_pay, bet, key) == (1, 176500, 3530, 10000, STORE)


def test_外れは払戻ゼロで確定する(patched):
    """外れは着順だけで決まる（配当を待つ理由がない）。"""
    conn = FakeConn(_rows(), _finish(3, 5, 6))
    patched["payouts"] = {RK: {}}
    assert m._settle_rank_7t1(conn, DATE) == (1, 0, 0)
    hit, payout, tf_pay, bet, _ = conn.updates[0]
    assert (hit, payout, tf_pay, bet) == (0, 0, 0, 10000)


def test_着順が揃っていなければ書かない(patched):
    """発走前・確定待ちを「不的中」として固定しない。"""
    conn = FakeConn(_rows(), _finish(1, 7))
    assert m._settle_rank_7t1(conn, DATE) == (0, 0, 0)
    assert conn.updates == []


def test_的中だが確定配当が無ければ次回へ回す(patched):
    """🔴 払戻0円で確定させると、的中がガミ（払戻<投資）として残る。"""
    patched["payouts"] = {RK: {}}
    conn = FakeConn(_rows(), _finish(1, 7, 4))
    assert m._settle_rank_7t1(conn, DATE) == (0, 0, 1)
    assert conn.updates == []


def test_投資額が入っていない候補行は対象外(patched):
    """SQL 側で `bet_amount > 0` に絞る＝売っていない候補は採点しない。"""
    conn = FakeConn([], _finish(1, 7, 4))
    assert m._settle_rank_7t1(conn, DATE) == (0, 0, 0)
    assert conn.updates == []


def test_買い目が読めない行は飛ばす(patched):
    conn = FakeConn(_rows("三単:1-3-2,4,6,5,7"), _finish(1, 7, 4))
    assert m._settle_rank_7t1(conn, DATE) == (0, 0, 0)
    assert conn.updates == []


# ---------------------------------------------------------------------------
# 呼び出しが外れていないか（黙って効かなくなる型の事故を防ぐ）
# ---------------------------------------------------------------------------

def test_採点バッチから呼ばれている():
    """🔴 呼び出しを外しても例外は出ず、7T1 の払戻だけが静かに消える。"""
    src = " ".join(m.__file__ and open(m.__file__, encoding="utf-8").read().split())
    assert "_settle_rank_7t1(conn, target_date)" in src
