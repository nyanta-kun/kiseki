#!/usr/bin/env python3
"""`order_swap_build.py` の3つの pkl（⓪現行 / ①τ適応 / ②τ適応+λr）を突き合わせる。

    python order_swap_cmp.py base.pkl tau.pkl both.pkl [--no-axis-gate]

🔴 知りたいのは **②−①**（λr の純増分）。①−⓪ は τ適応の検算に使う。
"""
from __future__ import annotations

import pickle
import sys
from statistics import median

import numpy as np

P0, P1, P2 = sys.argv[1], sys.argv[2], sys.argv[3]
AXIS_GATE = "--no-axis-gate" not in sys.argv
SCOPES = [("B_hit 単体", ("B_hit",)), ("F_hit 単体", ("F_hit",)),
          ("B_hit+F_hit", ("B_hit", "F_hit")), ("C_hit 単体（τ適応の検算）", ("C_hit",)),
          ("ラインナップ全体", None)]
RNG = np.random.default_rng(20260911)
NBOOT = 4000


def load(p):
    return pickle.load(open(p, "rb"))


def sold(rows, win, keys=None):
    return {r["i"]: r for r in rows
            if r["win"] == win and r["gate"]
            and (r.get("axis_ok", True) or not AXIS_GATE)
            and (keys is None or r["key"] in keys)}


def days(rows, win):
    return len({r["date"] for r in rows if r["win"] == win})


def summ(rs):
    if not rs:
        return None
    n = len(rs)
    hits = [r for r in rs if r["pay"] > 0]
    gami = [r for r in hits if r["pay"] < r["inv"]]
    shown = [r for r in rs if r["pay"] > r["inv"]]
    pays = sorted(r["pay"] for r in hits)
    inv = sum(r["inv"] for r in rs)
    cand = [r for r in rs if r.get("ocand")]
    return dict(
        n=n, k=sum(r["k"] for r in rs) / n,
        hit=len(hits) / n * 100,
        gami=len(gami) / len(hits) * 100 if hits else 0.0,
        shown=len(shown) / n * 100,
        med_pay=median(pays) if pays else 0.0,
        big=sum(1 for p in pays if p >= 100_000),
        roi=sum(r["pay"] for r in rs) / inv * 100 if inv else 0.0,
        lswap=sum(1 for r in rs if r["swapped"]) / n * 100,
        oswap=sum(1 for r in rs if r.get("ochg", 0) > 0) / n * 100,
        ocand=len(cand) / n * 100,
        ochg=(sum(r.get("ochg", 0) for r in rs)
              / max(1, sum(1 for r in rs if r.get("ochg", 0) > 0))),
        oviol=sum(r.get("oviol", 0) for r in rs),
        ogate=sum(1 for r in rs if r.get("ogate")) / n * 100,
        ord2=sum(1 for r in rs if r.get("sethit") and not r.get("exact")) / n * 100,
    )


def boot_delta(pairs):
    a = np.array([p[0] for p in pairs], float)
    b = np.array([p[1] for p in pairs], float)
    pt = (b.mean() - a.mean()) * 100
    n = len(a)
    idx = RNG.integers(0, n, size=(NBOOT, n))
    d = (b[idx].mean(1) - a[idx].mean(1)) * 100
    return pt, float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


HEAD = ("  {:14s} {:>6s} {:>6s} {:>7s} {:>6s} {:>8s} {:>9s} {:>8s} {:>7s} "
        "{:>7s} {:>7s} {:>7s} {:>6s} {:>6s} {:>7s}"
        .format("腕", "件/日", "点数", "的中%", "ガミ%", "表示的中%", "払戻中央",
                "10万+/日", "ROI%", "line%", "ord%", "候補%", "入替", "帯違反",
                "順序違い%"))


def row(name, s, nd):
    if s is None:
        return f"  {name:14s} (該当なし)"
    return (f"  {name:14s} {s['n']/nd:6.2f} {s['k']:6.2f} {s['hit']:7.2f} "
            f"{s['gami']:6.2f} {s['shown']:8.2f} {s['med_pay']:9,.0f} "
            f"{s['big']/nd:8.3f} {s['roi']:7.1f} {s['lswap']:7.1f} {s['oswap']:7.1f} "
            f"{s['ocand']:7.1f} {s['ochg']:6.2f} {s['oviol']:6d} {s['ord2']:7.2f}")


def main() -> None:
    A, B, D = load(P0), load(P1), load(P2)
    for win, label in (("confirm", "確認 2026-01〜08"), ("explore", "探索 2024-07〜2025-12")):
        nd = days(A, win)
        print("")
        print("=" * 140)
        print(f"=== {label}  （営業日 {nd}・軸信頼ゲート {'あり' if AXIS_GATE else 'なし'}）===")
        for scope, keys in SCOPES:
            s0, s1, s2 = sold(A, win, keys), sold(B, win, keys), sold(D, win, keys)
            common = sorted(set(s0) & set(s1) & set(s2))
            print("")
            print(f"-- {scope}  ⓪ n={len(s0):,} / ① n={len(s1):,} / ② n={len(s2):,} "
                  f"/ 共通 n={len(common):,} --")
            print(HEAD)
            print(row("⓪現行", summ([s0[i] for i in common]), nd))
            print(row("①τ適応", summ([s1[i] for i in common]), nd))
            print(row("②τ+λr", summ([s2[i] for i in common]), nd))
            sh = lambda s, i: s[i]["pay"] > s[i]["inv"]
            for tag, x, y in (("②−①", s1, s2), ("②−⓪", s0, s2), ("①−⓪", s0, s1)):
                pairs = [(sh(x, i), sh(y, i)) for i in common]
                pt, lo, hi = boot_delta(pairs)
                keep = sum(1 for a, b in pairs if a and b)
                brk = sum(1 for a, b in pairs if a and not b)
                sav = sum(1 for a, b in pairs if b and not a)
                print(f"  Δ表示的中 {tag} = {pt:+.2f}pt  95%CI [{lo:+.2f}, {hi:+.2f}]"
                      f"   維持 {keep} / 破壊 {brk} / 救済 {sav}")
            # `apply_line_swap` との重なり: ⓪側で差し替えが起きたかで割る
            if keys and keys != ("C_hit",):
                for tag, f in (("line 発動", lambda r: r["swapped"]),
                               ("line 非発動", lambda r: not r["swapped"])):
                    sub = [i for i in common if f(s0[i])]
                    if len(sub) < 30:
                        continue
                    pr = [(sh(s1, i), sh(s2, i)) for i in sub]
                    p2, l2, h2 = boot_delta(pr)
                    print(f"  [⓪側 {tag} n={len(sub):,}] "
                          f"① {np.mean([a for a, _ in pr])*100:.2f}% → "
                          f"② {np.mean([b for _, b in pr])*100:.2f}%  "
                          f"Δ {p2:+.2f} [{l2:+.2f}, {h2:+.2f}]")


if __name__ == "__main__":
    main()
