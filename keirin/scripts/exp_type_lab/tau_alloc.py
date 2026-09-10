#!/usr/bin/env python3
"""信頼度総和(τ)で点数を決める × 配分をどうするか（2026-09-10・ユーザー依頼）。

> 三連単も三連複も当たるのは1点だけ。オッズに応じたダッチより、上位からの
> **信頼度総和が幾つ以上**という買い方にすれば、少ない点数で厚く買えるのでは。

🔴 **選び方（τ）は 2026-08-31 に測り済み**（`coverage_framework_2026_08_31.md`）。
   現行の固定点数は τ≈0.29 の近似で、τ は単調ダイヤル・**ROI は 81〜85% で不動**。
   ここで新しく測るのは **配分（＝厚さ）** と「少ない点数で済むレース」の実態。

配分は4通り。すべて予算 10,000円・100円単位:
   conf   本番の `alloc='conf'`（floor_mult=2.0＝当たれば最低2倍を保証しつつ確率へ傾斜）
   dutch  本番の `alloc='dutch'`（1/予測オッズ に比例＝どの点が当たっても同額）
   prob   確率に比例（floor なし＝**最も厚い**）
   equal  均等
"""
from __future__ import annotations

import itertools
import pickle
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))

import common as C  # noqa: E402
from typef_racetype import ctx, _plan_for  # noqa: E402
from src.type_lab import (  # noqa: E402
    PLANS, Plan, SIGNBOARD_RACE_TYPES, allocate, build_legs, mean_expected_payout)

BUDGET, UNIT = 10_000, 100
MIN_MEAN_PAYOUT, MIN_POINT_ODDS = 20_000, 2.0
TAUS = (0.20, 0.25, 0.29, 0.35)
CACHE = Path("/tmp/tau_alloc_rows.pkl")
_CONF = Plan("_c", "X", "trifecta", "prob_top", 0, alloc="conf", floor_mult=2.0)
_DUTCH = Plan("_d", "X", "trifecta", "prob_top", 0, alloc="dutch", floor_mult=1.3)


def _prop(w, n_units):
    tot = sum(w)
    if tot <= 0:
        return None
    u = [1] * len(w)
    rest = n_units - len(w)
    if rest < 0:
        return None
    for j, x in enumerate(w):
        u[j] += int(rest * x / tot)
    while sum(u) < n_units:
        j = min(range(len(u)), key=lambda k: u[k] / max(w[k], 1e-12))
        u[j] += 1
    return u


def stakes_for(alloc, legs, pod, prb):
    if alloc == "conf":
        return allocate(legs, pod, prb, _CONF)
    if alloc == "dutch":
        return allocate(legs, pod, prb, _DUTCH)
    n = BUDGET // UNIT
    if len(legs) > n:
        return None
    if alloc == "equal":
        s = BUDGET // len(legs) // UNIT * UNIT
        return {c: s for c in legs}
    u = _prop([max(prb.get(c, 0.0), 1e-9) for c in legs], n)
    return None if u is None else {c: x * UNIT for c, x in zip(legs, u)}


def result(st, pod, x, trio=False):
    if not st:
        return None
    mean = mean_expected_payout(st, pod)
    gate = mean > MIN_MEAN_PAYOUT and min(float(pod[c]) for c in st) >= MIN_POINT_ODDS
    win, odds = ((x.win_t3, x.odds_t3) if trio else (x.win_tf, x.pay_tf))
    if trio:
        pay = float(st[win] * odds) if win in st else 0.0
    else:
        pay = float(st[win] / 100.0 * odds * 100.0) if win in st else 0.0
    return dict(k=len(st), inv=float(sum(st.values())), pay=pay, mean=float(mean),
                gate=gate, top=max(st.values()))


def build() -> list[dict]:
    z = C.board()
    rt = np.array([str(v) for v in z["RTYPE"]])
    tp = np.array([str(v) for v in z["TYPE"]])
    rows = []
    for win in ("explore", "confirm"):
        idx = [int(i) for i in C.select(None, win) if tp[int(i)] in "ABCDEF"]
        for n, i in enumerate(idx):
            if n % 5000 == 0:
                print(f"  {win} {n:,}/{len(idx):,}", flush=True)
            x = ctx(i)
            if x is None:
                continue
            trio_ok = False
            if tp[i] == "A":
                p = PLANS["A_trio"]
                lg = build_legs(x.shape, p, x.po_t3, x.pr_t3)
                trio_ok = bool(lg) and bool((result(allocate(lg, x.po_t3, x.pr_t3, p),
                                                   x.po_t3, x, True) or {}).get("gate"))
            key = _plan_for(tp[i], rt[i], x.shape.pw_ent, trio_ok,
                            tuple(SIGNBOARD_RACE_TYPES))
            plan = PLANS[key]
            trio = plan.bet_type == "trio"
            pod, prb = ((x.po_t3, x.pr_t3) if trio else (x.po_tf, x.pr_tf))
            legs = build_legs(x.shape, plan, pod, prb)
            cur = result(allocate(legs, pod, prb, plan), pod, x, trio) if legs else None
            if cur is None:
                continue
            # ── τ 規則（三連単の全210点を確率降順に積む・帯なし）──
            order = sorted(x.po_tf, key=lambda c: -x.pr_tf.get(c, 0.0))
            cum, sel = 0.0, []
            arms = {"cur": cur}
            for t in TAUS:
                while cum < t and len(sel) < 30 and len(sel) < len(order):
                    c = order[len(sel)]
                    sel.append(c)
                    cum += x.pr_tf.get(c, 0.0)
                legs_t = list(sel)
                for a in ("conf", "dutch", "prob", "equal"):
                    arms[f"t{t}_{a}"] = result(stakes_for(a, legs_t, x.po_tf, x.pr_tf),
                                               x.po_tf, x, False)
            rows.append(dict(win=win, date=x.date, type=tp[i], plan=key, arms=arms))
    return rows


if CACHE.exists():
    ROWS = pickle.load(CACHE.open("rb"))
else:
    ROWS = build()
    pickle.dump(ROWS, CACHE.open("wb"))
W = {w: [r for r in ROWS if r["win"] == w] for w in ("explore", "confirm")}


def agg(sel, nd):
    if not sel:
        return None
    inv = sum(a["inv"] for a in sel); pay = sum(a["pay"] for a in sel)
    pays = sorted(a["pay"] for a in sel if a["pay"] > 0)
    return dict(n=len(sel), perday=len(sel) / nd, k=np.mean([a["k"] for a in sel]),
                shown=np.mean([a["pay"] >= a["inv"] for a in sel]) * 100,
                roi=pay / inv * 100, med=float(np.median(pays)) if pays else 0.0,
                big=sum(1 for p in pays if p >= 100_000) / nd,
                top=np.mean([a["top"] for a in sel]))


def main() -> None:
    for wn, lab in (("confirm", "確認 2026-01〜08（本番相当）"),
                    ("explore", "探索 2024-07〜2025-12")):
        rows = W[wn]
        nd = len({r["date"] for r in rows})
        print("\n" + "=" * 112)
        print(f"=== {lab}   母集団 {len(rows):,}レース / {nd}日")
        print("=" * 112)
        print(f"    {'腕':22s} {'件/日':>6s} {'点数':>5s} {'最大賭け金':>9s} {'表示的中%':>9s} "
              f"{'ROI%':>7s} {'払戻中央':>9s} {'10万+/日':>8s}")
        B = agg([r["arms"]["cur"] for r in rows if r["arms"]["cur"]["gate"]], nd)
        print(f"    {'現行（型ごとの点数）':22s} {B['perday']:6.2f} {B['k']:5.1f}"
              f" {B['top']:9,.0f} {B['shown']:9.2f} {B['roi']:7.1f} {B['med']:9,.0f}"
              f" {B['big']:8.3f}")
        for t in TAUS:
            for a, an in (("conf", "本番と同じ conf"), ("dutch", "ダッチ"),
                          ("prob", "確率比例(厚い)"), ("equal", "均等")):
                sel = [r["arms"][f"t{t}_{a}"] for r in rows
                       if r["arms"].get(f"t{t}_{a}") and r["arms"][f"t{t}_{a}"]["gate"]]
                s = agg(sel, nd)
                if not s:
                    continue
                print(f"    {f'τ={t} / {an}':22s} {s['perday']:6.2f} {s['k']:5.1f}"
                      f" {s['top']:9,.0f} {s['shown']:9.2f} {s['roi']:7.1f} {s['med']:9,.0f}"
                      f" {s['big']:8.3f}")

        # ── 「少ない点数で済むレース」の実態 ─────────────────────────────
        print(f"\n  ■ τ=0.29 に到達するのに要る点数 k で分ける（配分は本番と同じ conf）")
        print(f"    {'k':10s} {'件/日':>6s} {'割合%':>6s} {'最大賭け金':>9s} {'表示的中%':>9s} "
              f"{'ROI%':>7s} {'払戻中央':>9s} {'10万+/日':>8s}")
        key = "t0.29_conf"
        pool = [r for r in rows if r["arms"].get(key) and r["arms"][key]["gate"]]
        for lo, hi, nm in ((1, 2, "1〜2点"), (3, 4, "3〜4点"), (5, 6, "5〜6点"),
                           (7, 8, "7〜8点"), (9, 12, "9〜12点"), (13, 99, "13点以上")):
            sub = [r["arms"][key] for r in pool if lo <= r["arms"][key]["k"] <= hi]
            s = agg(sub, nd)
            if not s:
                continue
            print(f"    {nm:10s} {s['perday']:6.2f} {len(sub)/len(pool)*100:6.2f}"
                  f" {s['top']:9,.0f} {s['shown']:9.2f} {s['roi']:7.1f} {s['med']:9,.0f}"
                  f" {s['big']:8.3f}")

        # ── 少点数だけを売る腕（厳対照つき）────────────────────────────
        print(f"\n  ■ 『少ない点数で厚く』だけを売る（k<=K のレースだけ・配分は conf）")
        print(f"    {'腕':16s} {'件/日':>6s} {'点数':>5s} {'最大賭け金':>9s} {'表示的中%':>9s} "
              f"{'ROI%':>7s} {'払戻中央':>9s} {'10万+/日':>8s}   {'厳対照20本（同件数・同点数で無作為な点）':>40s}")
        for K in (3, 4, 5, 6):
            sub = [r for r in pool if r["arms"][key]["k"] <= K]
            s = agg([r["arms"][key] for r in sub], nd)
            if not s:
                continue
            # 厳対照: 同じレース・同じ点数を、確率と無関係に無作為な点で買う
            cs, cr = [], []
            for seed in range(20):
                rng = np.random.default_rng(seed)
                v = []
                for r in sub:
                    a = r["arms"][key]
                    # 確率と無関係な「無作為な k 点」の的中確率は k/210
                    v.append(1.0 if rng.random() < a["k"] / 210.0 else 0.0)
                cs.append(np.mean(v) * 100)
            print(f"    {f'k<={K}':16s} {s['perday']:6.2f} {s['k']:5.1f} {s['top']:9,.0f}"
                  f" {s['shown']:9.2f} {s['roi']:7.1f} {s['med']:9,.0f} {s['big']:8.3f}"
                  f"   無作為な同点数の的中期待 {np.median(cs):5.2f}%")


if __name__ == "__main__":
    main()
