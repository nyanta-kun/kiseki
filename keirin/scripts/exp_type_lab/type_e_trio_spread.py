#!/usr/bin/env python3
"""型E: 帯の中で「同じ3車の並びを何点まで買うか」（2026-09-04・ユーザー提案）。

## 発端

> 現在の買い目では E は多くのハズレを発生させている。**想定のオッズ帯となった
> レースにおいて外れている場合、買い目の組み方を見直しできないか**。

`E_hit` は「予測30倍以上から確率上位14点」。確率順に取ると**同じ3車の別の並び**が
何点も入る（既知: 14点が張る三連複集合は 8）。順序は事前に読めない
（`type_e_2026_09_01.md` §3・OOS AUC 0.54）ので、**重複した並びに使う点を
別の集合へ回したほうがカバレッジが広がるのではないか**、が本稿の問い。

これは 2026-09-04 の上帯（並び違いの補完）と**正反対の操作**。あちらは重複側へ
寄せて前向き実測で悪化した（`overlay_upper_band_2026_09_04.md` §9.6）。

🔴 **対照を置く**（帯の中から無作為に同数）。件数を変えない振り分けでも、
   「並べ替えただけで良く見える」ことがあるため。
🔴 確認窓(2026)が本番相当。ROI では決めない（表示的中で見る）。
"""
from __future__ import annotations

import random
import sys
from collections import Counter
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))

import common as C  # noqa: E402
from typef_racetype import (ctx, AXIS_GATE_MIN,  # noqa: E402
                            MIN_MEAN_PAYOUT, MIN_POINT_ODDS)
from src.type_lab import PLANS, allocate, mean_expected_payout  # noqa: E402

BAND = 30.0
K = 14


def cand(x):
    """帯の中の目を確率降順で。"""
    c = [tuple(k) for k, v in x.po_tf.items()
         if v and float(v) >= BAND and len(set(k)) == 3]
    c.sort(key=lambda k: -float(x.pr_tf.get(k, 0.0)))
    return c


def pick(x, cap: int, k: int = K):
    """1つの3車集合から最大 `cap` 点まで。`cap=0` は制限なし（＝現行）。"""
    out, per = [], Counter()
    for p in cand(x):
        t = frozenset(p)
        if cap and per[t] >= cap:
            continue
        out.append(p)
        per[t] += 1
        if len(out) >= k:
            break
    return out if len(out) >= 2 else None


def pick_random(x, k: int, seed: int):
    c = cand(x)
    if len(c) < k:
        return None
    return random.Random(seed).sample(c, k)


def run(x, legs):
    """本番と同じ配分・ゲートで採点する。"""
    if not legs:
        return None
    plan = PLANS["E_hit"]
    st = allocate(legs, x.po_tf, x.pr_tf, plan)
    if not st:
        return None
    if mean_expected_payout(st, x.po_tf) <= MIN_MEAN_PAYOUT:
        return None
    if min(float(x.po_tf[c]) for c in st) < MIN_POINT_ODDS:
        return None
    pay = float(st[x.win_tf] / 100.0 * x.pay_tf * 100.0) if x.win_tf in st else 0.0
    return dict(date=x.date, inv=float(sum(st.values())), pay=pay, k=len(st),
                mean=mean_expected_payout(st, x.po_tf),
                trios=len({frozenset(c) for c in st}),
                win_in_band=float(x.po_tf.get(x.win_tf, 0.0)) >= BAND,
                win_trio_bought=frozenset(x.win_tf) in {frozenset(c) for c in st})


def summarize(recs, nd, label):
    if not recs:
        print(f"  {label:24s} 該当なし")
        return
    s = C.summarize(recs, nd)
    trios = sum(r["trios"] for r in recs) / len(recs)
    band = sum(1 for r in recs if r["win_in_band"]) / len(recs) * 100
    got = sum(1 for r in recs if r["win_trio_bought"]) / len(recs) * 100
    print(f"  {label:24s} {s['perday']:5.2f} {s['k']:5.1f} {trios:5.2f} "
          f"{s['hit']:6.2f} {s['gami']:5.2f} {s['shown']:8.2f}% {s['roi']:6.1f} "
          f"{s['med_pay']:8,.0f} {band:6.1f}% {got:7.1f}%")


def main() -> None:
    z = C.board()
    axs = z["AXIS_SUM"].astype(float)
    for label, win in (("探索 2024-07〜2025-12", "explore"),
                       ("確認 2026-01〜08 (本番相当)", "confirm")):
        idx = [int(i) for i in C.select("E", win)
               if axs[int(i)] >= AXIS_GATE_MIN["E_hit"]]
        nd = C.days_of(C.select(None, win))
        rows = []
        for i in idx:
            x = ctx(i)
            if x is not None:
                rows.append(x)
        print("\n" + "=" * 118)
        print(f"███ 型E 集合の広げ方  {label}  n={len(rows):,}R / {nd}日")
        print(f"  {'腕':24s} {'件/日':>5s} {'点数':>5s} {'集合':>5s} "
              f"{'的中%':>6s} {'ガミ':>5s} {'表示的中%':>8s} {'ROI':>6s} "
              f"{'払戻中央':>8s} {'決着が帯内':>7s} {'集合を買えた':>8s}")
        arms = {"現行（確率上位14点）": lambda x: pick(x, 0),
                "集合上限3": lambda x: pick(x, 3),
                "集合上限2": lambda x: pick(x, 2),
                "集合上限1（14集合）": lambda x: pick(x, 1)}
        got = {}
        for name, f in arms.items():
            recs = [r for r in (run(x, f(x)) for x in rows) if r]
            got[name] = recs
            summarize(recs, nd, name)
        # 対照: 帯の中から無作為に14点
        ms, mr = [], []
        for seed in range(20):
            recs = [r for r in (run(x, pick_random(x, K, seed * 977 + 13))
                                for x in rows) if r]
            if recs:
                s = C.summarize(recs, nd)
                ms.append(s["shown"]); mr.append(s["roi"])
        if ms:
            base = C.summarize(got["現行（確率上位14点）"], nd)
            print(f"  {'（対照）無作為14点':24s} {'':5s} {'':5s} {'':5s} "
                  f"{'':6s} {'':5s} {np.median(ms):8.2f}% {np.median(mr):6.1f}"
                  f"   ← 現行が勝ち "
                  f"{sum(base['shown'] > v for v in ms)}/20")


if __name__ == "__main__":
    main()
