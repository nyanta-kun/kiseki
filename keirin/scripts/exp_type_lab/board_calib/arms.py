#!/usr/bin/env python3
"""組番確率の較正の腕比較（事前登録 docs/type_lab/board_calibration_2026_10_28.md §5・§6）。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/board_calib/arms.py [--start 2026-01-01 --end 2026-08-04]

前提: fit.py が data/exp_bet_review/board_calib_feats.npz・board_calib_coef.json を作っていること。
台は data/exp_bet_review/race_type_board.npz（lineup_sim の /tmp ハードコードを差し替える）。
確認窓 2026 の予測オッズは台の PO（本番 odds_tf・train_end 2025-12-31）。
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
D = REPO / "data" / "exp_bet_review"

import lineup_sim as S  # noqa: E402
import lineup_arms as R  # noqa: E402
from lineup_sim import _G, settle  # noqa: E402
from src.type_lab import HIGHPAY_SLOTS_PER_DAY  # noqa: E402

N_BOOT = 2000


def load_board():
    z = np.load(D / "race_type_board.npz", allow_pickle=True)
    S._Z = {k: z[k] for k in S._NEED}
    return S._Z


def pstar_rows(coef: dict, model: str):
    """board index -> p*（PERMS 順・210）。確認窓の行だけ。"""
    f = np.load(D / "board_calib_feats.npz")
    LP, LQ, G, SPLIT, IDX = f["LP"], f["LQ"], f["G"], f["SPLIT"], f["IDX"]
    out = {}
    for r in np.flatnonzero(SPLIT == "conf"):
        c = coef["M1"] if model == "M1" else coef["M2_semi" if G[r] else "M2_other"]
        s = c["a"] * LP[r] + c["b"] * LQ[r]
        s = np.exp(s - s.max())
        out[int(IDX[r])] = s / s.sum()
    return out


def with_probs(x, p):
    y = copy.copy(x)
    y.pr_tf = {S.PERMS[t]: float(p[t]) for t in range(210)}
    _, y.pr_t3 = S._fold_to_trio(x.po_tf, y.pr_tf)
    return y


def sum_p(x, main):
    pr = x.pr_t3 if main["trio"] else x.pr_tf
    return sum(pr.get(c, 0.0) for c in main["stakes"])


def run_prio(plans: dict, idx, cache, prio):
    """lineup_arms.run の写し。違いは日次上限の残す順を prio(x, main) にできることだけ。

    prio=None なら本番の cap_priority（lineup_arms.run と同一の結果になることを main で確認する）。
    """
    byday = defaultdict(list)
    for i in idx:
        x = cache[i]
        if x is not None:
            byday[x.date].append(x)
    recs = []
    for day, races in sorted(byday.items()):
        cand = []
        for x in races:
            main, _ = R.race_rows(x, plans)
            if main is None:
                continue
            passes = _G.passes_axis_gate(main["plan"], float(x.shape.axis_sum), 7)
            cand.append((x, main, passes))
        judged = [c for c in cand if not _G.daily_cap_exempt(c[0].rtype, c[0].cupg)
                  and not _G.daily_cap_exempt_plan(c[1]["plan"])]
        budget = max(1, int(len(judged) * float(_G.DAILY_CAP_RACE_FRACTION))) if judged else None
        if prio is None:
            key = lambda c: -_G.cap_priority(c[1]["plan"], float(c[0].shape.axis_sum), c[0].rp_sd)  # noqa: E731
        else:
            key = lambda c: -prio(c[0], c[1])  # noqa: E731
        order = sorted(cand, key=key)
        n_capped, dropped = 0, []
        for x, main, passes in order:
            exempt = (_G.daily_cap_exempt(x.rtype, x.cupg)
                      or _G.daily_cap_exempt_plan(main["plan"]))
            if not passes:
                dropped.append(x); continue
            if budget is not None and not exempt and n_capped >= budget:
                dropped.append(x); continue
            if not exempt:
                n_capped += 1
            inv, pay = settle(x, main["stakes"], main["trio"])
            recs.append(dict(day=day, plan=main["plan"], inv=inv, pay=pay, mean=main["mean"],
                             n=main["n"], slot="main", race_key=x.key))
        n_done = 0
        for x in dropped:
            if n_done >= HIGHPAY_SLOTS_PER_DAY:
                break
            hp = R.highpay_row(x, plans, n_done)
            if not hp:
                continue
            inv, pay = settle(x, hp["stakes"], hp["trio"])
            recs.append(dict(day=day, plan=hp["plan"], inv=inv, pay=pay, mean=hp["mean"],
                             n=hp["n"], slot="highpay", race_key=x.key))
            n_done += 1
    return recs


def day_arrays(recs, days):
    pos = {d: i for i, d in enumerate(days)}
    a = {k: np.zeros(len(days)) for k in ("n", "hit", "inv", "pay")}
    for r in recs:
        i = pos[r["day"]]
        a["n"][i] += 1; a["hit"][i] += r["pay"] > r["inv"]
        a["inv"][i] += r["inv"]; a["pay"][i] += r["pay"]
    return a


def roi_excl(recs, top=5):
    inv = sum(r["inv"] for r in recs)
    pays = sorted((r["pay"] for r in recs), reverse=True)
    return (sum(pays) - sum(pays[:top])) / inv * 100


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-08-04")
    args = ap.parse_args()
    z = load_board()
    coef = json.load(open(D / "board_calib_coef.json"))
    m = ((z["TYPE"] != "") & (z["TRIO_WIN"] >= 0) & np.isfinite(z["TRIO_PAY"])
         & z["OKPRED"] & (z["DATE"] >= args.start) & (z["DATE"] <= args.end))
    idx = np.flatnonzero(m)
    base = {int(i): S.ctx(int(i)) for i in idx}
    ps = {"M1": pstar_rows(coef, "M1"), "M2": pstar_rows(coef, "M2")}
    ok = [int(i) for i in idx if base.get(int(i)) is not None and int(i) in ps["M2"]]
    days = sorted({base[i].date for i in ok})
    print(f"対象 {len(idx)}R / 組めて p* あり {len(ok)}R / {len(days)}日", flush=True)
    c1 = {i: with_probs(base[i], ps["M1"][i]) for i in ok}
    c2 = {i: with_probs(base[i], ps["M2"][i]) for i in ok}

    # 写しの正しさ: prio=None の run_prio が lineup_arms.run と同一
    ref = R.run("current", {}, ok, base)
    mine = run_prio({}, ok, base, None)
    same = [(r["race_key"], r["plan"], r["inv"], r["pay"]) for r in ref] == \
           [(r["race_key"], r["plan"], r["inv"], r["pay"]) for r in mine]
    print("run_prio(None) == lineup_arms.run:", same, len(ref), flush=True)
    assert same

    arms = {
        "current": ref,
        "C1": run_prio({}, ok, c1, None),
        "C2": run_prio({}, ok, c2, None),
        "C2P": run_prio({}, ok, c2, sum_p),
    }
    nd = len(days)
    print(R.HEAD + f"{'回収(上位5除)':>12}")
    for k, recs in arms.items():
        print(R.line(k, R.summarize(recs, nd)) + f"{roi_excl(recs):>12.1f}", flush=True)
    for k in ("C2", "C2P"):
        print(f"--- {k} プラン別")
        g = defaultdict(list)
        for r in arms[k]:
            g[r["plan"]].append(r)
        for p in sorted(g, key=lambda p: -len(g[p])):
            print("   " + R.line(p, R.summarize(g[p], nd)))
    print("--- current プラン別")
    g = defaultdict(list)
    for r in arms["current"]:
        g[r["plan"]].append(r)
    for p in sorted(g, key=lambda p: -len(g[p])):
        print("   " + R.line(p, R.summarize(g[p], nd)))

    # 差の CI（開催日単位の対応ブートストラップ）
    rng = np.random.default_rng(20261028)
    A0 = day_arrays(arms["current"], days)
    for k in ("C1", "C2", "C2P"):
        A1 = day_arrays(arms[k], days)
        dh, dr = [], []
        for _ in range(N_BOOT):
            s = rng.integers(0, nd, nd)
            h0 = A0["hit"][s].sum() / A0["n"][s].sum(); h1 = A1["hit"][s].sum() / A1["n"][s].sum()
            r0 = A0["pay"][s].sum() / A0["inv"][s].sum(); r1 = A1["pay"][s].sum() / A1["inv"][s].sum()
            dh.append((h1 - h0) * 100); dr.append((r1 - r0) * 100)
        ph = (A1["hit"].sum() / A1["n"].sum() - A0["hit"].sum() / A0["n"].sum()) * 100
        pr = (A1["pay"].sum() / A1["inv"].sum() - A0["pay"].sum() / A0["inv"].sum()) * 100
        print(f"{k} − current: 表示的中 {ph:+.2f}pt [{np.percentile(dh, 2.5):+.2f}, {np.percentile(dh, 97.5):+.2f}]"
              f"  回収 {pr:+.2f}pt [{np.percentile(dr, 2.5):+.2f}, {np.percentile(dr, 97.5):+.2f}]"
              f"  回収(上位5除) {roi_excl(arms[k]) - roi_excl(arms['current']):+.2f}pt", flush=True)


if __name__ == "__main__":
    main()
