#!/usr/bin/env python3
"""二軸の当たり外れを条件で分け、外れ側だけ別の買い目／別の二軸へ替えられるか（2026-09-29）。

ユーザー提案:
  ① 二軸は3着内にそろったのに買い目が外れたレース → 条件で分け、外している側だけ
     別の買い目へ（当たっている側は現行を維持）
  ② 二軸を外したレース → 外すケースを事前に検知し、別の二軸で組めないか

母集団: 7車・その型で入稿する1商品（`sell_plans_for`）・入稿ゲート＋軸信頼ゲート通過・
日次上限なし（`two_axis_swap_skip.rows_for` と同じ作り）。
二軸 = `shape.order[:2]`（p3 上位2車＝型ラボの ◎○）。

① の腕: 並べ替え `apply_order_swap` を当該プランにも掛ける（本番は B_hit / F_hit だけ）。
   条件セルは探索窓で選び、確認窓へそのまま当てる。
② は読みの層: セルごとに「どの2車を軸にすると両方3着内にそろうか」を探索窓で選び、
   確認窓で p3 上位2車と比べる。
"""
from __future__ import annotations

import os
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lineup_sim  # noqa: F401  (pickle が lineup_sim.Ctx を引く)
import src.type_lab as TL
from lineup_sim import _G, build, gate_ok, settle
from src.type_lab import PLANS, sell_plans_for

EXPLORE = ("2024-07-01", "2025-12-31")
CONFIRM = ("2026-01-01", "2026-08-04")
WINS = {"探索": EXPLORE, "確認": CONFIRM}
CACHE = os.environ.get("TYPE_LAB_CTX", "/tmp/type_lab_ctx_fixed.pkl")
OUT = Path(__file__).resolve().parent / "two_axis_cond_rows.pkl"
ORIG_SWAP = TL.ORDER_SWAP_PLANS


def win_of(d):
    for w, (a, b) in WINS.items():
        if a <= d <= b:
            return w
    return None


_TRIO_OK: dict = {}


def trio_ok(x):
    if x.key not in _TRIO_OK:
        got = build(x, PLANS["A_trio"])
        _TRIO_OK[x.key] = bool(got and gate_ok(got[0], got[1], got[3]))
    return _TRIO_OK[x.key]


def line_feats(shape):
    a1, a2 = shape.order[0], shape.order[1]
    for ln in shape.lines:
        if a1 in ln and a2 in ln:
            i1, i2 = ln.index(a1), ln.index(a2)
            return "同", ("軸1前" if i1 < i2 else "軸2前"), len(ln)
    return "別", "-", 0


def outcome(x, stakes, trio):
    a1, a2 = x.shape.order[0], x.shape.order[1]
    w = x.win_tf
    top3 = set(w)
    n_ax = int(a1 in top3) + int(a2 in top3)
    inv, pay = settle(x, stakes, trio)
    if pay > inv:
        cls = "的中"
    elif pay > 0:
        cls = "ガミ"
    elif n_ax == 2:
        if trio:
            cls = "そろい・組違い"
        else:
            cls = ("そろい・順序違い" if any(set(c) == top3 for c in stakes)
                   else "そろい・組違い")
    elif n_ax == 1:
        cls = "片軸"
    else:
        cls = "軸崩壊"
    return cls, inv, pay, n_ax


def make_rows():
    cache = pickle.load(open(CACHE, "rb"))
    rows = []
    for n, (i, x) in enumerate(cache.items()):
        if x is None:
            continue
        w = win_of(x.date)
        if w is None:
            continue
        t = x.shape.type_label
        sell = sell_plans_for(t, 7, x.rtype, pw_ent=x.shape.pw_ent,
                              trio_ok=trio_ok(x) if t == "A" else None)
        if not sell:
            continue
        plan = sell[0]
        TL.ORDER_SWAP_PLANS = ORIG_SWAP
        got = build(x, plan)
        if not got or not gate_ok(got[0], got[1], got[3]):
            continue
        stakes, _odds, used, _mean = got
        if not _G.passes_axis_gate(used.key, float(x.shape.axis_sum), 7):
            continue
        trio = used.bet_type == "trio"
        cls, inv, pay, n_ax = outcome(x, stakes, trio)
        r = dict(i=i, day=x.date, win=w, plan=plan.key, used=used.key, type=t,
                 inv=inv, pay=pay, cls=cls, n_ax=n_ax, n=len(stakes),
                 axis_sum=float(x.shape.axis_sum), pw_ent=float(x.shape.pw_ent),
                 win_gap=float(x.shape.win_gap), order=tuple(x.shape.order),
                 top3=tuple(x.win_tf), pay_tf=x.pay_tf, trio=trio)
        r["same"], r["front"], r["lnlen"] = line_feats(x.shape)
        a1, a2 = x.shape.order[0], x.shape.order[1]
        r["a1in"], r["a2in"] = a1 in x.win_tf, a2 in x.win_tf
        r["win_po"] = float(x.po_tf.get(x.win_tf, float("nan")))
        r["setcov"] = (x.win_t3 in stakes) if trio else any(set(c) == set(x.win_tf) for c in stakes)
        r["orderok"] = (not trio) and (x.win_tf in stakes)
        r["arare"] = int(x.shape.arare)
        # 並べ替え腕（三連単のみ・本番で既に掛かっているプランは同一）
        if not trio and used.key not in ORIG_SWAP:
            TL.ORDER_SWAP_PLANS = frozenset(ORIG_SWAP | {used.key})
            g2 = build(x, plan)
            TL.ORDER_SWAP_PLANS = ORIG_SWAP
            if g2 and gate_ok(g2[0], g2[1], g2[3]):
                _c, i2, p2, _ = outcome(x, g2[0], False)
                r["sw_inv"], r["sw_pay"] = i2, p2
                r["sw_changed"] = set(g2[0]) != set(stakes)
            else:
                r["sw_inv"], r["sw_pay"], r["sw_changed"] = inv, pay, False
        else:
            r["sw_inv"], r["sw_pay"], r["sw_changed"] = inv, pay, False
        rows.append(r)
        if n % 5000 == 0:
            print(f"  {n:,}/{len(cache):,}", flush=True)
    pickle.dump(rows, open(OUT, "wb"))
    return rows


if __name__ == "__main__":
    rows = make_rows()
    print(len(rows), "rows ->", OUT)
