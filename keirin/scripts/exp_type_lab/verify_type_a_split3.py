#!/usr/bin/env python3
"""🔴 本番コードで §12 の数字が再現するかの検算（2026-08-31）。

実験台（`type_a_split3.py`）は腕を手書きで組んでいる。**出荷した実装が同じ商品を
作っているか**は、`src.type_lab` の `sell_plans_for` / `build_legs` / `allocate` を
そのまま呼んで確かめないと分からない（CLAUDE.md「測る前に本番コードを読む」の裏返し）。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/verify_type_a_split3.py
"""
from __future__ import annotations

import itertools
import math
import re
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import numpy as np                                            # noqa: E402
from src.database import get_connection                       # noqa: E402
from src.marquee import is_fill_target                        # noqa: E402
from src.stake_allocation import MIN_MEAN_PAYOUT, MIN_POINT_ODDS   # noqa: E402
from src.type_lab import (                                    # noqa: E402
    BUDGET, PLANS, RaceShape, allocate, build_legs, mean_expected_payout,
    min_expected_payout, sell_plans_for, win_entropy)
import importlib.util                                          # noqa: E402

_s = importlib.util.spec_from_file_location(
    "gate", REPO.parent / "backend/src/services/keirin_type_lab_gate.py")
G = importlib.util.module_from_spec(_s); _s.loader.exec_module(G)   # type: ignore

CANON = list(itertools.permutations(range(1, 8), 3))
CIDX = {c: i for i, c in enumerate(CANON)}
WINDOWS = {"探索 2025": ("2025-01-01", "2025-12-31"),
           "確認 2026": ("2026-01-01", "2026-08-04")}


def load():
    boards, bidx = {}, {}
    for k in ("prod", "vint"):
        z = np.load(f"/tmp/tf20_board_{k}.npz", allow_pickle=True)
        boards[k] = {n: z[n] for n in ("PROB", "PO", "PAY", "KEY")}
        bidx[k] = {str(x): i for i, x in enumerate(boards[k]["KEY"])}
    with get_connection() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT race_key, race_date, race_type, axis_sum, arare, gap, p3_order, "
            "       win_combo FROM type_lab_picks WHERE mode='paper' AND plan_key='A_hit' "
            "  AND settled_at IS NOT NULL AND n_entries=7 AND p3_order IS NOT NULL "
            "  AND win_combo IS NOT NULL")]
        rows = [d for d in rows
                if is_fill_target(d.get("race_type"), None)
                or G.passes_axis_gate("A_hit",
                                      float(d["axis_sum"]) if d["axis_sum"] is not None else None, 7)]
        keys = sorted({d["race_key"] for d in rows})
        ent, trio = defaultdict(dict), defaultdict(dict)
        for i in range(0, len(keys), 400):
            ch = keys[i:i + 400]; ph = ",".join("?" * len(ch))
            for r in c.execute(f"SELECT race_key, frame_no, pred_win_pct FROM wt_entries "
                               f"WHERE race_key IN ({ph})", tuple(ch)):
                d = dict(r)
                if d["pred_win_pct"] is not None:
                    ent[d["race_key"]][int(d["frame_no"])] = float(d["pred_win_pct"])
            for r in c.execute(f"SELECT race_key, combination, odds_value FROM wt_odds "
                               f"WHERE bet_type='trio' AND race_key IN ({ph})", tuple(ch)):
                d = dict(r)
                trio[d["race_key"]][frozenset(int(x) for x in re.findall(r"\d+", d["combination"]))] \
                    = float(d["odds_value"])
    out = []
    for d in rows:
        date = str(d["race_date"])
        bk = "vint" if date <= "2025-12-31" else "prod"
        i = bidx[bk].get(d["race_key"])
        pw = ent.get(d["race_key"], {})
        if i is None or len(pw) != 7:
            continue
        order = tuple(int(x) for x in str(d["p3_order"]).replace(",", "-").split("-") if x)
        f = tuple(int(x) for x in str(d["win_combo"]).split("-"))
        if len(order) != 7 or len(f) != 3:
            continue
        out.append(dict(
            date=date, order=order, f=f,
            shape=RaceShape("A", float(d["axis_sum"] or 0), int(d["arare"] or 0),
                            float(d["gap"] or 0), True, order, win_entropy(pw)),
            PO=boards[bk]["PO"][i], PROB=boards[bk]["PROB"][i],
            PAY=float(boards[bk]["PAY"][i]), trio_final=trio.get(d["race_key"], {})))
    return out


def fold_trio(po, prob):
    o, p = {}, {}
    for c in itertools.combinations(range(1, 8), 3):
        fc = frozenset(c)
        inv = s = 0.0
        for t in itertools.permutations(c):
            v = float(po[CIDX[t]])
            if v > 0:
                inv += 1.0 / v
            s += float(prob[CIDX[t]])
        if inv > 0:
            o[fc] = 1.0 / inv
        p[fc] = s
    return o, p


def play(d, plan):
    tf_o = {t: float(d["PO"][CIDX[t]]) for t in CANON if float(d["PO"][CIDX[t]]) > 0}
    tf_p = {t: float(d["PROB"][CIDX[t]]) for t in CANON}
    if plan.bet_type == "trio":
        odds, prob = fold_trio(d["PO"], d["PROB"])
    else:
        odds, prob = tf_o, tf_p
    legs = build_legs(d["shape"], plan, odds, prob)
    if not legs:
        return None
    stakes = allocate(legs, odds, prob, plan)
    if not stakes:
        return None
    legs = [c for c in legs if c in stakes]
    if mean_expected_payout(stakes, odds) <= MIN_MEAN_PAYOUT:
        return None
    if min(float(odds[c]) for c in legs) < MIN_POINT_ODDS:
        return None
    inv = sum(int(stakes[c]) for c in legs)
    pay = 0.0
    if plan.bet_type == "trio":
        w = frozenset(d["f"])
        if w in stakes:
            fo = d["trio_final"].get(w)
            if fo is None:
                return None
            pay = stakes[w] * fo
    else:
        if d["f"] in stakes:
            pay = stakes[d["f"]] / 100.0 * d["PAY"]
    return dict(date=d["date"], inv=inv, pay=pay, k=len(legs))


def kpi(recs, nd):
    if not recs:
        return None
    inv = sum(r["inv"] for r in recs); pay = sum(r["pay"] for r in recs)
    h = [r for r in recs if r["pay"] > 0]
    sh = [r for r in h if r["pay"] > r["inv"]]
    ps = sorted(r["pay"] for r in h)
    return dict(perday=len(recs)/nd, shown=len(sh)/len(recs)*100,
                med=median(ps) if ps else 0, roi=pay/inv*100 if inv else 0,
                big=sum(1 for x in ps if x >= 100_000)/nd)


def main() -> int:
    data = load()
    print(f"台 {len(data):,}R（本番の build_legs / allocate / sell_plans_for を通す）")
    for win, (lo, hi) in WINDOWS.items():
        rs = [d for d in data if lo <= d["date"] <= hi]
        nd = len({d["date"] for d in rs})
        print(f"\n=== {win}  {len(rs):,}R / {nd}日 ===")
        print(f"  {'構成':<22}{'件/日':>7}{'表示的中':>9}{'払戻中央':>10}{'ROI':>8}{'10万+/日':>10}"
              f"{'穴%':>7}{'三連複%':>8}")
        for lab, use_ana in (("現行相当（A_hit のみ）", False), ("3分割（本番実装）", True)):
            recs, n_ana, n_trio = [], 0, 0
            for d in rs:
                trio_ok = play(d, PLANS["A_trio"]) is not None
                key = sell_plans_for(
                    "A", 7, None,
                    pw_ent=(d["shape"].pw_ent if use_ana else None),
                    trio_ok=(trio_ok if use_ana else None))[0].key
                r = play(d, PLANS[key])
                if r is None and key != "A_hit":
                    r = play(d, PLANS["A_hit"])          # 通らなければ現行へ
                    key = "A_hit"
                if r:
                    recs.append(r)
                    n_ana += key == "A_ana"
                    n_trio += key == "A_trio"
            s = kpi(recs, nd)
            print(f"  {lab:<22}{s['perday']:>7.2f}{s['shown']:>8.2f}%{s['med']:>10,.0f}"
                  f"{s['roi']:>8.1f}{s['big']:>10.3f}{n_ana/len(recs)*100:>7.1f}"
                  f"{n_trio/len(recs)*100:>8.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
