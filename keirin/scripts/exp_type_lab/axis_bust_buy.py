#!/usr/bin/env python3
"""「軸が飛ぶレースを検出して上位以外から買う」を**全型**で商品化できるか（2026-09-03）。

`axis_bust_cond.py` で検出は両窓で再現した（pw_ent・AUC 0.66・上位10%で型A 2.1倍）。
ここでは買いに変換する。

腕（いずれも本番の `build_legs`/`allocate`/入稿ゲートを通す）:
  現行      … `sell_plans_for` 相当（看板枠・型A3分割込み）
  ANA k点   … 軸1（p3 1位）を外した残り6車から確率上位 k 点・三連単ダッチ
  ANA2 k点  … 軸1・軸2 を外した残り5車から確率上位 k 点

🔴 **無作為対照を必ず置く**（`race_filter_2026_08_27.md`）。件数を減らすと CI が
   広がるので、上振れを効果と誤読しないため同数を無作為に20本引く。
🔴 確認窓(2026)が本番相当（予測オッズ `odds_tf_n7` の train_end 2025-12-31）。
"""
from __future__ import annotations
import sys, pathlib, random
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
import common as C
from typef_racetype import (ctx, _run_named, _plan_for, AXIS_GATE_MIN,
                            MIN_MEAN_PAYOUT, MIN_POINT_ODDS)
from src.type_lab import (PLANS, Plan, SIGNBOARD_RACE_TYPES, allocate,
                          build_legs, mean_expected_payout)

# ── ANA 系の腕（軸を外して買う）──
ANA: dict[str, Plan] = {}
for k in (5, 8, 12, 18):
    ANA[f"ANA{k} 軸1外し確率上位{k}点"] = Plan(
        "x", "?", "trifecta", "bust_top", 0, max_legs=k, alloc="dutch")


def run_plan(x, plan: Plan):
    pod, prb = ((x.po_t3, x.pr_t3) if plan.bet_type == "trio" else (x.po_tf, x.pr_tf))
    legs = build_legs(x.shape, plan, pod, prb)
    if not legs:
        return None
    st = allocate(legs, pod, prb, plan)
    if not st:
        return None
    if mean_expected_payout(st, pod) <= MIN_MEAN_PAYOUT:
        return None
    if min(float(pod[c]) for c in st) < MIN_POINT_ODDS:
        return None
    inv = float(sum(st.values()))
    if plan.bet_type == "trio":
        pay = float(st[x.win_t3] * x.odds_t3) if x.win_t3 in st else 0.0
    else:
        pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
    return dict(date=x.date, inv=inv, pay=pay, k=len(st),
                mean=mean_expected_payout(st, pod))


def main(types: str = "ABCDEF", qs=(0.90, 0.80)) -> None:
    z = C.board()
    rt = np.array([str(v) for v in z["RTYPE"]])
    tp = np.array([str(v) for v in z["TYPE"]])
    axs = z["AXIS_SUM"].astype(float)
    sign_rt = tuple(SIGNBOARD_RACE_TYPES)

    for label, win in (("探索 2024-07〜2025-12 (in-sample)", "explore"),
                       ("確認 2026-01〜08 (本番相当)", "confirm")):
        nd = C.days_of(C.select(None, win))
        print("\n" + "=" * 122)
        print(f"███ {label}   全体日数 {nd}日")
        for t in types:
            idx = [int(i) for i in C.select(t, win)]
            rows = []
            for i in idx:
                x = ctx(i)
                if x is None:
                    continue
                pw = x.shape.pw_ent
                cur_key = _plan_for(t, rt[i], pw, _run_named(x, "A_trio") is not None
                                    if t == "A" else False, sign_rt)
                if axs[i] < AXIS_GATE_MIN.get(cur_key, 0.0):
                    cur = None
                else:
                    cur = _run_named(x, cur_key)
                arms = {n: run_plan(x, p) for n, p in ANA.items()}
                rows.append(dict(i=i, pw=pw, cur=cur, arms=arms))
            if not rows:
                continue
            pws = np.array([r["pw"] for r in rows])
            print("\n" + "-" * 122)
            print(f"--- 型{t}  n={len(rows):,}R ---")
            print(C.HEAD)
            print(C.line("現行（全レース）",
                         C.summarize([r["cur"] for r in rows if r["cur"]], nd)))
            for n in ANA:
                print(C.line(f"{n}（全レース）",
                             C.summarize([r["arms"][n] for r in rows if r["arms"][n]], nd)))
            for q in qs:
                thr = np.quantile(pws, q)
                sel = [r for r in rows if r["pw"] >= thr]
                nsel = len(sel)
                print(f"  ▼ pw_ent 上位{int((1-q)*100)}%（thr={thr:.4f}・{nsel}R）")
                print(C.line("  現行（同レース）",
                             C.summarize([r["cur"] for r in sel if r["cur"]], nd)))
                for n in ANA:
                    s = C.summarize([r["arms"][n] for r in sel if r["arms"][n]], nd)
                    print(C.line(f"  {n}", s))
                    # 無作為対照 20 本（同数・同型）
                    rng = random.Random(1234)
                    wins = ctrl_roi = ctrl_shown = 0
                    rs, rr = [], []
                    for seed in range(20):
                        rng2 = random.Random(seed * 977 + 13)
                        pick = rng2.sample(rows, nsel)
                        cs = C.summarize([r["arms"][n] for r in pick if r["arms"][n]], nd)
                        if not cs.get("n"):
                            continue
                        rs.append(cs["shown"]); rr.append(cs["roi"])
                        wins += s["shown"] > cs["shown"]
                        ctrl_roi += s["roi"] > cs["roi"]
                    if rs:
                        print(f"      └ 無作為対照20本 中央 表示的中 {np.median(rs):5.2f}%"
                              f" / ROI {np.median(rr):5.1f}  →  検出が勝ち"
                              f" 表示的中 {wins}/20・ROI {ctrl_roi}/20")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "ABCDEF")
