#!/usr/bin/env python3
"""§24 の三者同時確率は **7C の相手選定へ移植できるか**（2026-08-23）。

## なぜ先に測るのか

§24 の利得は「5候補から **1点** を選ぶ」形で出したもの。
ところが 7C は `rank_7c_select_legs` が `p3 >= 0.15` で足切りするだけで、
実測 **4点63% / 5点37%（平均4.37点）**＝**5点中4〜5点を買う総流しに近い**。
並べ替えても集合がほぼ同じなら**利得はゼロ**になる。
🔴 **実装する前に、点数を揃えた置き換えで差が出るかを測る。**

## 腕（点数は必ず揃える）

| 腕 | 相手の選び方 |
|---|---|
| 現行 | `p3 >= 0.15` の車（＝限界確率の足切り） |
| **同時確率** | 同じ点数を **`P(3車すべて3着内)` の上位**から採る |
| 参考: 1点 | 同時確率の最上位 **1点だけ**（§24 の形） |
| 参考: 総流し | 5点すべて |

🔴 一次指標は**的中率**（7C の KPI）。ROI も出すが判断は的中優先。
🔴 学習・検定は年をまたぐ独立窓（`--swap` で逆向きも）。
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7t1_rank_wt import _load_finishes  # noqa: E402
from scripts.exp_trio_joint_partner import (  # noqa: E402
    build_A, fit, load_any, load_boards, load_entries)
from src.result_top3 import winning_trifectas  # noqa: E402
from src.strategy_wt import (  # noqa: E402
    RANK_7C_LEG_P3_MIN, RANK_7C_LEGS_MIN, rank_7c_cut_legs_by_gap,
    rank_7c_select_legs, unit_stake)

PAYOUT_RATE = 0.7485


def ci_diff(days, B=4000, seed=97):
    v = np.array([[d[0], d[1], d[2]] for d in days.values()], float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(v), size=(B, len(v)))
    tot = v[idx, 0].sum(1)
    d = np.sort(v[idx, 2].sum(1) / tot - v[idx, 1].sum(1) / tot)
    return d[int(B * .025)], d[int(B * .975)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default="data/exp/trio_rank_cache.jsonl")
    ap.add_argument("--test", default="data/exp/tf_shape_cache4.jsonl")
    ap.add_argument("--rounds", type=int, default=400)
    ap.add_argument("--swap", action="store_true")
    args = ap.parse_args()

    tr, te = load_any(args.train), load_any(args.test)
    if args.swap:
        tr, te = te, tr
    def span(v):
        return f"{min(r['date'] for r in v)}〜{max(r['date'] for r in v)}"
    print(f"学習 {len(tr):,}R（{span(tr)}） / 検定 {len(te):,}R（{span(te)}）")
    print(f"7C の足切り p3 >= {RANK_7C_LEG_P3_MIN} / 最低 {RANK_7C_LEGS_MIN} 点\n")

    kt, ke = [r["key"] for r in tr], [r["key"] for r in te]
    ent_tr, ent_te = load_entries(kt), load_entries(ke)
    fin_tr, fin_te = _load_finishes(kt), _load_finishes(ke)
    Xtr, ytr, _ = build_A(tr, ent_tr, fin_tr)
    Xte, yte, mte = build_A(te, ent_te, fin_te)
    m = fit(Xtr, ytr, args.rounds)
    pred = m.predict(Xte)
    board = load_boards(ke)

    by_race = defaultdict(list)
    axes, date_of = {}, {}
    for (key, date, a1, a2, c, rk), p in zip(mte, pred):
        by_race[key].append((float(p), c, rk))
        axes[key] = (a1, a2)
        date_of[key] = date
    p3_of = {r["key"]: r["p3"] for r in te}
    wins_of = {}
    for r in te:
        o3 = fin_te.get(r["key"])
        if o3:
            wins_of[r["key"]] = {frozenset(w) for w in winning_trifectas(o3)}

    arms = ["現行(p3落差カット)", "同時確率(同点数)", "同時確率1点", "総流し5点"]
    rows = {a: [] for a in arms}
    npt = defaultdict(int)
    n = same = 0
    for key, v in by_race.items():
        bd = board.get(key); w = wins_of.get(key)
        if not bd or not w or len(v) != 5:
            continue
        a1, a2 = axes[key]
        p3 = p3_of[key]
        others = [c for _, c, _ in v]
        # 🔴 **本番の買い方をそのまま通す**（選抜 → 落差カット → △削り）。
        #    選抜だけを再現すると平均4.37点になるが、7C が実際に買うのは
        #    落差カット後の平均2.60点。ここを間違えたのが §29 の誤り。
        sel = rank_7c_select_legs(others, p3)
        if len(sel) < RANK_7C_LEGS_MIN:      # 7C はこのレースを買わない
            continue
        cur = rank_7c_cut_legs_by_gap(sel, p3)
        if not cur:
            continue
        jnt = [c for _, c, _ in sorted(v, key=lambda x: -x[0])]
        picks = {"現行(p3落差カット)": cur, "同時確率(同点数)": jnt[:len(cur)],
                 "同時確率1点": jnt[:1], "総流し5点": jnt}
        ks = {a: [frozenset((a1, a2, c)) for c in legs] for a, legs in picks.items()}
        if any(any(k not in bd for k in v_) for v_ in ks.values()):
            continue
        n += 1
        npt[len(cur)] += 1
        same += int(set(cur) == set(picks["同時確率(同点数)"]))
        for a, legs in picks.items():
            st = unit_stake(len(legs))
            hit = any(k in w for k in ks[a])
            pay = sum(int(bd[k] * 100) * st // 100 for k in ks[a] if k in w)
            rows[a].append((date_of[key], int(hit), pay, len(legs) * st))

    print(f"7C が買うレース {n:,}R"
          f"   点数分布 " + " / ".join(f"{k}点 {npt[k]/n:.0%}" for k in sorted(npt)) +
          f"   平均 {sum(k*c for k, c in npt.items())/n:.2f}点")
    print(f"   同時確率が現行と**同じ集合**になった率: {same/n:.1%}\n")
    print(f"{'腕':>18}{'的中%':>9}{'ROI':>9}{'的中Δ':>24}{'ROIΔ':>24}")
    base_h = base_r = None
    for a in arms:
        seg = rows[a]
        dh = defaultdict(lambda: [0, 0, 0])
        dr = defaultdict(lambda: [0.0, 0.0, 0.0])
        for d, h, p, b in seg:
            z = dh[d]; z[0] += 1; z[2] += h
            z = dr[d]; z[0] += b; z[2] += p
        for (d, h, p, b), (d2, h2, p2, b2) in zip(seg, rows["現行(p3落差カット)"]):
            dh[d][1] += h2
            dr[d][1] += p2
        hit = sum(x[1] for x in seg) / len(seg)
        roi = sum(x[2] for x in seg) / sum(x[3] for x in seg)
        if a == "現行(p3落差カット)":
            base_h, base_r = hit, roi
            print(f"{a:>18}{hit:>9.2%}{roi:>9.1%}{'':>24}{'':>24}")
            continue
        lh, uh = ci_diff(dh)
        lr, ur = ci_diff(dr)
        fh = "🟢" if lh > 0 else ("🔴" if uh < 0 else "")
        fr = "🟢" if lr > 0 else ("🔴" if ur < 0 else "")
        print(f"{a:>18}{hit:>9.2%}{roi:>9.1%}"
              f"{f'{(hit-base_h)*100:+.2f}pt[{lh*100:+.2f},{uh*100:+.2f}]{fh}':>24}"
              f"{f'{(roi-base_r)*100:+.1f}pt[{lr*100:+.1f},{ur*100:+.1f}]{fr}':>24}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
