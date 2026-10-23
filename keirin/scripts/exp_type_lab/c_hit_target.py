#!/usr/bin/env python3
"""C_hit の狙う払戻（目標額）の掃引（2026-10-24）。

本番 `src/type_lab.py` をそのまま呼び、`lineup_arms.run` と同じく日次上限・
軸信頼ゲート・高額枠まで再現する。変えるのは `C_hit` の Plan と入稿ゲートだけ。

腕
  current       今の C_hit（5万ダッチ・落ちたら旧構成へ戻す）
  sbT           看板と同じ詰め方（確率順・超える目は飛ばす）で目標 T 円。ゲート落ちは旧構成へ戻す
  sbT_g{G}      同上・旧構成へ戻さず「当たったときの悪い側 >= G 円」で判定
  tierT_g{G}    段（固め/広め）と同じ詰め方（確率順・途中で止める・3〜8点）で目標 T 円・判定は同上
"""
from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lineup_arms as LA
import lineup_sim as S

import src.type_lab as TL
from src.type_lab import PLANS, tier_realized_min_payout

GATE_FB0 = dict(TL.GATE_FALLBACK)
GATE_OK0 = LA.gate_ok


def legs_rows(stakes, odds):
    return [{"stake": float(v), "pred_odds": float(odds[k])} for k, v in stakes.items()]


def arm_plan(name: str):
    """(C_hit の Plan, 旧構成へ戻すか, 悪い側ゲート円 or None)"""
    if name == "current":
        return PLANS["C_hit"], True, None
    base, *opt = name.split("_")
    g = int(opt[0][1:]) if opt and opt[0].startswith("g") else None
    t = int(base[len("tier") if base.startswith("tier") else 2:])
    if base.startswith("tier"):
        p = replace(PLANS["T_mid"], key="C_hit", type_label="C", target=t)
    else:
        p = replace(PLANS["C_hit"], target=t)
    return p, g is None, g


SELL0 = LA.sell_plans_for


def run_arm(name, idx, cache):
    # 末尾 "+noA" で A_hit を売らない（型A の A_trio / A_ana は残す）
    no_a = name.endswith("+noA")
    name = name.removesuffix("+noA")
    LA.sell_plans_for = ((lambda *a, **k: [p for p in SELL0(*a, **k) if p.key != "A_hit"])
                         if no_a else SELL0)
    try:
        return _run_arm(name, idx, cache)
    finally:
        LA.sell_plans_for = SELL0


def _run_arm(name, idx, cache):
    plan, keep_fb, g = arm_plan(name)
    TL.GATE_FALLBACK = GATE_FB0 if keep_fb else {k: v for k, v in GATE_FB0.items() if k != "C_hit"}
    cur = {}

    def gate(stakes, odds, mean):
        x = cur["x"]
        if g is not None and cur.get("plan") == "C_hit":
            w = tier_realized_min_payout(legs_rows(stakes, odds), x.rtype, float(x.shape.axis_sum))
            return (w is None or w >= g) and min(float(odds[c]) for c in stakes) >= S.MIN_POINT_ODDS
        return GATE_OK0(stakes, odds, mean)

    LA.gate_ok = gate
    orig_build = LA.build

    def build(x, pl, plans_override=None):
        cur["x"] = x; cur["plan"] = pl.key
        got = orig_build(x, pl)
        if got and pl.key == "C_hit":
            cur.setdefault("struct", {})[x.key] = got[2].structure
        return got

    LA.build = build
    try:
        recs = LA.run(name, {"C_hit": plan}, idx, cache)
    finally:
        LA.build = orig_build; LA.gate_ok = GATE_OK0; TL.GATE_FALLBACK = GATE_FB0
    struct = cur.get("struct", {})
    for r in recs:
        r["struct"] = struct.get(r["race_key"]) if r["plan"] == "C_hit" else None
    return recs


def summ(recs, ndays, cache, by_key):
    if not recs:
        return "該当なし"
    inv = sum(r["inv"] for r in recs); pay = sum(r["pay"] for r in recs)
    shown = sum(r["pay"] > r["inv"] for r in recs)
    gami = sum(0 < r["pay"] <= r["inv"] for r in recs)
    pays = [r["pay"] for r in recs if r["pay"] > r["inv"]]
    sp = 0.0; hits = 0
    for r in recs:
        x = cache[by_key[r["race_key"]]]
        prob = x.pr_t3 if r["trio"] else x.pr_tf
        sp += sum(prob.get(k, 0.0) for k in r["stakes"])
        hits += r["pay"] > 0
    fb = sum(1 for r in recs if r.get("struct") and r["struct"] == "prob_top")
    return (f"{len(recs)/ndays:5.2f}件/日 表示的中{100*shown/len(recs):5.1f}% ROI{100*pay/inv:5.1f}% "
            f"払戻中央{statistics.median(pays) if pays else 0:>7,.0f} ガミ{100*gami/len(recs):4.1f}% "
            f"点数{statistics.mean(r['n'] for r in recs):4.1f} 実際/見込み{hits/sp:4.2f} 旧構成{100*fb/len(recs):4.1f}%")


def boot_diff(a, b, days, n=2000, seed=0):
    """日ブートストラップ: 腕 b − 腕 a の (表示的中pt, ROIpt)。同じ日の集合で比べる。"""
    rng = np.random.default_rng(seed)
    def per_day(recs):
        d = defaultdict(lambda: [0, 0, 0.0, 0.0])
        for r in recs:
            v = d[r["day"]]; v[0] += 1; v[1] += r["pay"] > r["inv"]; v[2] += r["inv"]; v[3] += r["pay"]
        return d
    da, db = per_day(a), per_day(b)
    days = sorted(days)
    A = np.array([da.get(x, [0, 0, 0, 0]) for x in days], float)
    B = np.array([db.get(x, [0, 0, 0, 0]) for x in days], float)
    out = []
    for _ in range(n):
        i = rng.integers(0, len(days), len(days))
        sa, sb = A[i].sum(0), B[i].sum(0)
        out.append((100 * (sb[1] / sb[0] - sa[1] / sa[0]), 100 * (sb[3] / sb[2] - sa[3] / sa[2])))
    o = np.array(out)
    return np.percentile(o, [2.5, 97.5], axis=0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-07-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--arms", default="current,sb22000,sb25000,sb30000,sb35000,sb40000,"
                                      "sb25000_g10000,tier22000_g10000,tier22000_g15000,tier25000_g10000")
    args = ap.parse_args()
    z = S.board()
    m = ((z["TYPE"] != "") & (z["TRIO_WIN"] >= 0) & np.isfinite(z["TRIO_PAY"])
         & z["OKPRED"] & (z["DATE"] >= args.start) & (z["DATE"] <= args.end))
    idx = [int(i) for i in np.flatnonzero(m)]
    cache = {i: S.ctx(i) for i in idx}
    ok = [i for i in idx if cache[i] is not None]
    by_key = {cache[i].key: i for i in ok}
    days = {cache[i].date for i in ok}
    ndays = len(days)
    print(f"{args.start}〜{args.end}  {len(ok)}R / {ndays}日", flush=True)
    base = None
    for a in args.arms.split(","):
        recs = run_arm(a, ok, cache)
        c = [r for r in recs if r["plan"] == "C_hit"]
        line = f"{a:<18} C_hit {summ(c, ndays, cache, by_key)}  | 全商品 {summ(recs, ndays, cache, by_key)[:44]}"
        if a == "current":
            base = (c, recs)
            ah = [r for r in recs if r["plan"] == "A_hit"]
            line += f"\n{'':18} (参考) A_hit {summ(ah, ndays, cache, by_key)}"
        else:
            lo, hi = boot_diff(base[0], c, days)
            lo2, hi2 = boot_diff(base[1], recs, days)
            line += (f"\n{'':18} C_hit Δ表示的中[{lo[0]:+.1f},{hi[0]:+.1f}] ΔROI[{lo[1]:+.1f},{hi[1]:+.1f}]"
                     f"  全商品 Δ表示的中[{lo2[0]:+.2f},{hi2[0]:+.2f}] ΔROI[{lo2[1]:+.1f},{hi2[1]:+.1f}]")
        print(line, flush=True)


if __name__ == "__main__":
    main()
