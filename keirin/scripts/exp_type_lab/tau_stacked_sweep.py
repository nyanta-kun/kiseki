#!/usr/bin/env python3
"""`TAU_ADAPTIVE_MAX_LEGS` の掃引を「上限 → 取引条件」の表1枚にする（2026-09-11）。

入力は `tau_stacked_build.py` が各水準で出した pkl。基準は現行（main）の pkl。
**同一レース対応比較**（両側が入稿ゲートを通ったレースだけで Δ を取る）。

    python tau_stacked_sweep.py 現行.pkl 12:m12.pkl 13:m13.pkl ...
"""
from __future__ import annotations

import pickle
import sys
from statistics import median

import numpy as np

RNG = np.random.default_rng(20260911)
NB = 4000
BASE = sys.argv[1]
ARMS = [a.split(":", 1) for a in sys.argv[2:]]


def load(p):
    return pickle.load(open(p, "rb"))


def sold(rows, win, key=None):
    return {r["i"]: r for r in rows if r["win"] == win and r["gate"] and r["axis_ok"]
            and (key is None or r["key"] == key)}


def days(rows, win):
    return len({r["date"] for r in rows if r["win"] == win})


def boot(pairs):
    a = np.array([p[0] for p in pairs], float)
    b = np.array([p[1] for p in pairs], float)
    idx = RNG.integers(0, len(a), size=(NB, len(a)))
    d = (b[idx].mean(1) - a[idx].mean(1)) * 100
    return (b.mean() - a.mean()) * 100, float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


def stats(rs):
    n = len(rs)
    hits = [r for r in rs if r["pay"] > 0]
    gami = [r for r in hits if r["pay"] < r["inv"]]
    pays = sorted(r["pay"] for r in hits)
    return dict(n=n, k=sum(r["k"] for r in rs) / n,
                shown=sum(1 for r in rs if r["pay"] > r["inv"]) / n * 100,
                gami=len(gami) / len(hits) * 100 if hits else 0.0,
                med=median(pays) if pays else 0.0,
                big=sum(1 for p in pays if p >= 100_000),
                roi=sum(r["pay"] for r in rs) / sum(r["inv"] for r in rs) * 100)


def main() -> None:
    B = load(BASE)
    arms = [(lab, load(p)) for lab, p in ARMS]
    for win, label in (("confirm", "確認 2026-01〜08"), ("explore", "探索 2024-07〜2025-12")):
        nd = days(B, win)
        for scope, key in (("C_hit 単体", "C_hit"), ("ラインナップ全体", None)):
            sb = sold(B, win, key)
            s0 = stats(list(sb.values()))
            print("")
            print(f"=== {label} / {scope}（営業日 {nd}）===")
            if key:
                print(f"  {'上限':>6s} {'件/日':>6s} {'平均点数':>8s} {'上限張付':>8s} "
                      f"{'表示的中%':>9s} {'Δpt':>7s} {'95%CI':>18s} {'払戻中央':>9s} "
                      f"{'ガミ%':>6s} {'ROI%':>6s} {'維持/破壊/救済':>16s}")
                print(f"  {'現行':>6s} {s0['n']/nd:6.2f} {s0['k']:8.2f} {'—':>8s} "
                      f"{s0['shown']:9.2f} {'—':>7s} {'—':>18s} {s0['med']:9,.0f} "
                      f"{s0['gami']:6.2f} {s0['roi']:6.1f} {'—':>16s}")
            else:
                print(f"  {'上限':>6s} {'件/日':>6s} {'表示的中%':>9s} {'Δpt':>7s} "
                      f"{'95%CI':>18s} {'払戻中央':>9s} {'10万+/日':>9s} {'ROI%':>6s}")
                print(f"  {'現行':>6s} {s0['n']/nd:6.2f} {s0['shown']:9.2f} {'—':>7s} "
                      f"{'—':>18s} {s0['med']:9,.0f} {s0['big']/nd:9.3f} {s0['roi']:6.1f}")
            for lab, rows in arms:
                st = sold(rows, win, key)
                common = sorted(set(sb) & set(st))
                rs = [st[i] for i in common]
                s = stats(rs)
                pairs = [(sb[i]["pay"] > sb[i]["inv"], st[i]["pay"] > st[i]["inv"])
                         for i in common]
                pt, lo, hi = boot(pairs)
                cap = (sum(1 for r in rs if r["k"] >= int(lab.split("/")[0]))
                       / len(rs) * 100) if key and lab.split("/")[0].isdigit() else float("nan")
                keep = sum(1 for a, b in pairs if a and b)
                brk = sum(1 for a, b in pairs if a and not b)
                sav = sum(1 for a, b in pairs if b and not a)
                if key:
                    print(f"  {lab:>6s} {len(st)/nd:6.2f} {s['k']:8.2f} {cap:7.1f}% "
                          f"{s['shown']:9.2f} {pt:+7.2f} {f'[{lo:+.2f},{hi:+.2f}]':>18s} "
                          f"{s['med']:9,.0f} {s['gami']:6.2f} {s['roi']:6.1f} "
                          f"{f'{keep}/{brk}/{sav}':>16s}")
                else:
                    print(f"  {lab:>6s} {len(st)/nd:6.2f} {s['shown']:9.2f} {pt:+7.2f} "
                          f"{f'[{lo:+.2f},{hi:+.2f}]':>18s} {s['med']:9,.0f} "
                          f"{s['big']/nd:9.3f} {s['roi']:6.1f}")

    # 点数分布（下限が効いているかの確認）
    print("")
    print("=== 選ばれた点数の下側（`TAU_ADAPTIVE_MIN_LEGS` が効いているか）===")
    import collections
    for lab, rows in arms:
        for win in ("confirm", "explore"):
            ks = [r["k"] for r in sold(rows, win, "C_hit").values()]
            c = collections.Counter(ks)
            low = {k: c[k] for k in sorted(c) if k <= 8}
            print(f"  上限{lab:>4s} {win:8s} 最小 {min(ks):2d}点  "
                  f"8点以下 {sum(low.values())/len(ks)*100:5.2f}%  {low}")


if __name__ == "__main__":
    main()
