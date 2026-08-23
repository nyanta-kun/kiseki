#!/usr/bin/env python3
"""軸2の置き換えを **3着以下との差 × ライン** で条件分けする（2026-08-23）。

## ユーザー指摘

> 軸2の見直しについて **3着以下との差**、**ライン**も考慮した条件分けが必要に思う。

🔴 一律の「◎○除外」は −5.42pt、信頼度十分位で切っても最下位10%で −1.17pt と
   プラスに転じなかった。だが**どちらも「置き換えの中身」を見ていない**——
   置き換えの損は「軸2と代わりの車の差」で決まり、代わりの車が機能するかは
   「軸1と同じラインか」で決まるはず。**その2軸で切る。**

## 切り方（すべて朝に確定・オッズ非依存）

| 記号 | 量 |
|---|---|
| `gap_rep` | `p3[軸2] − p3[代替車]`（**払う代償**。小さいほど置き換えが安い） |
| `sl_rep` | 代替車が**軸1と同一ライン**か（軸1を支えられるか） |
| `sl_axes` | 現行の軸1・軸2が同一ラインか（今のペアがライン戦で噛んでいるか） |

母集団は **二軸が◎○ ∧ `axis_sum <= 1.40`**（堅いレースは商品が売っていない）。
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7t1_rank_wt import _load_finishes  # noqa: E402
from scripts.exp_axis1_bust_stratified import load_rich  # noqa: E402
from scripts.exp_axis_prod_baseline import load_cache4  # noqa: E402
from scripts.exp_trio_joint_partner import fit, load_any  # noqa: E402
from scripts.exp_trio_pair_model import build_rows as build_pairs  # noqa: E402
from scripts.exp_trio_pair_model import load_entries  # noqa: E402
from src.result_top3 import winning_trifectas  # noqa: E402
from src.strategy_wt import rank_7s_select_axis  # noqa: E402


def ci_diff(days, B=3000, seed=97):
    v = np.array([[d[0], d[1], d[2]] for d in days.values()], float)
    if len(v) < 5:
        return -9.0, 9.0
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(v), size=(B, len(v)))
    tot = v[idx, 0].sum(1)
    d = np.sort(v[idx, 2].sum(1) / tot - v[idx, 1].sum(1) / tot)
    return d[int(B * .025)], d[int(B * .975)]


def report(title, groups):
    """groups: [(ラベル, [row,...])]"""
    print(f"\n===== {title} =====")
    print(f"{'条件':>22}{'件数':>8}{'現行':>9}{'置換後':>9}{'（対現行）':>26}")
    for lab, sub in groups:
        if len(sub) < 80:
            continue
        d = defaultdict(lambda: [0, 0, 0])
        for r in sub:
            z = d[r["date"]]
            z[0] += 1
            z[1] += r["y"]
            z[2] += int(r["a1_in"] and r["rep"] in r["t3"])
        n = len(sub)
        hc = sum(z[1] for z in d.values()) / n
        hd = sum(z[2] for z in d.values()) / n
        lo, hi = ci_diff(d)
        f = "🟢" if lo > 0 else ("🔴" if hi < 0 else "")
        print(f"{lab:>22}{n:>8,}{hc:>9.2%}{hd:>9.2%}"
              f"{f'Δ{(hd-hc)*100:+.2f}pt [{lo*100:+.1f},{hi*100:+.1f}]{f}':>26}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default="data/exp/trio_rank_cache.jsonl")
    ap.add_argument("--test", default="data/exp/tf_shape_cache4.jsonl")
    ap.add_argument("--rounds", type=int, default=400)
    ap.add_argument("--axis-sum-max", type=float, default=1.40)
    args = ap.parse_args()

    te = load_cache4(args.test)
    fin = _load_finishes([r["key"] for r in te])
    te = [r for r in te if r["key"] in fin and len(r["p3"]) >= 7]
    ent = load_rich([r["key"] for r in te])
    tr = load_any(args.train)
    Xtr, ytr, _ = build_pairs(tr, load_entries([r["key"] for r in tr]),
                              _load_finishes([r["key"] for r in tr]))
    pm = fit(Xtr, ytr, args.rounds)
    te_rows = [dict(key=r["key"], date=r["date"], p3=r["p3"],
                    order=sorted(r["p3"], key=lambda c: (-r["p3"][c], c))) for r in te]
    Xp, _, mp = build_pairs(te_rows, load_entries([r["key"] for r in te_rows]), fin)
    pair = defaultdict(dict)
    for (key, _, a, b, _, _), p in zip(mp, pm.predict(Xp)):
        pair[key][frozenset((a, b))] = float(p)

    rows = []
    for r in te:
        sel = rank_7s_select_axis(r["pw"], r["p3"], r["bad"])
        if sel is None or r["key"] not in pair:
            continue
        a1, a2, _ = sel
        mk = r["mark"]
        hon = next((c for c, x in mk.items() if x == 1), None)
        tai = next((c for c, x in mk.items() if x == 2), None)
        if hon is None or tai is None or a1 not in (hon, tai) or a2 not in (hon, tai):
            continue
        p3 = r["p3"]
        if p3[a1] + p3[a2] > args.axis_sum_max:
            continue
        cand = [c for c in p3 if c not in (a1, hon, tai)]
        if not cand:
            continue
        rep = max(cand, key=lambda c: pair[r["key"]].get(frozenset((a1, c)), 0.0))
        e = ent[r["key"]]
        lg = lambda c: e[c]["lg"] if c in e else None  # noqa: E731
        t3 = {c for w in winning_trifectas(fin[r["key"]]) for c in w}
        rows.append(dict(
            key=r["key"], date=r["date"], y=int(a1 in t3 and a2 in t3),
            a1_in=int(a1 in t3), t3=t3, rep=rep,
            gap_rep=p3[a2] - p3[rep],
            sl_rep=int(lg(a1) is not None and lg(a1) == lg(rep)),
            sl_axes=int(lg(a1) is not None and lg(a1) == lg(a2)),
        ))
    print(f"母集団 {len(rows):,}R（二軸が◎○ ∧ axis_sum<={args.axis_sum_max}）")
    print(f"  現行の二軸的中 {np.mean([r['y'] for r in rows]):.2%}"
          f" / 代替車が軸1と同ライン {np.mean([r['sl_rep'] for r in rows]):.1%}"
          f" / 現行の二軸が同ライン {np.mean([r['sl_axes'] for r in rows]):.1%}")

    q = np.quantile([r["gap_rep"] for r in rows], [.25, .5, .75])
    report("① 3着以下との差（gap_rep = p3[軸2] − p3[代替車]）", [
        (f"Q1 差小 (〜{q[0]:.3f})", [r for r in rows if r["gap_rep"] < q[0]]),
        (f"Q2 ({q[0]:.3f}〜{q[1]:.3f})", [r for r in rows if q[0] <= r["gap_rep"] < q[1]]),
        (f"Q3 ({q[1]:.3f}〜{q[2]:.3f})", [r for r in rows if q[1] <= r["gap_rep"] < q[2]]),
        (f"Q4 差大 ({q[2]:.3f}〜)", [r for r in rows if r["gap_rep"] >= q[2]]),
    ])
    report("② ライン", [
        ("代替が軸1と同ライン", [r for r in rows if r["sl_rep"]]),
        ("代替が別ライン", [r for r in rows if not r["sl_rep"]]),
        ("現行二軸が同ライン", [r for r in rows if r["sl_axes"]]),
        ("現行二軸が別ライン", [r for r in rows if not r["sl_axes"]]),
    ])
    report("③ 差 × ライン（差が小さく、代替が軸1と同ライン＝置き換えが最も安い）", [
        ("差小 ∧ 代替同ライン", [r for r in rows if r["gap_rep"] < q[1] and r["sl_rep"]]),
        ("差小 ∧ 代替別ライン", [r for r in rows if r["gap_rep"] < q[1] and not r["sl_rep"]]),
        ("差大 ∧ 代替同ライン", [r for r in rows if r["gap_rep"] >= q[1] and r["sl_rep"]]),
        ("差大 ∧ 代替別ライン", [r for r in rows if r["gap_rep"] >= q[1] and not r["sl_rep"]]),
    ])
    report("⑤ 3条件の重なり（差小 ∧ 代替が軸1と同ライン ∧ 現行二軸が別ライン）", [
        ("3条件すべて", [r for r in rows
                     if r["gap_rep"] < q[1] and r["sl_rep"] and not r["sl_axes"]]),
        ("差小∧代替同L∧二軸同L", [r for r in rows
                            if r["gap_rep"] < q[1] and r["sl_rep"] and r["sl_axes"]]),
        ("それ以外すべて", [r for r in rows
                      if not (r["gap_rep"] < q[1] and r["sl_rep"])]),
    ])
    report("④ 現行二軸が別ライン（噛んでいない）× 差", [
        ("二軸別ライン ∧ 差小", [r for r in rows if not r["sl_axes"] and r["gap_rep"] < q[1]]),
        ("二軸別ライン ∧ 差大", [r for r in rows if not r["sl_axes"] and r["gap_rep"] >= q[1]]),
        ("二軸同ライン ∧ 差小", [r for r in rows if r["sl_axes"] and r["gap_rep"] < q[1]]),
    ])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
