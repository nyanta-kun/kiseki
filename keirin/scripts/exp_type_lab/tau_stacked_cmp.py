#!/usr/bin/env python3
"""`tau_stacked_build.py` が出した2つの pkl（現行 / τ適応）を突き合わせる。

    python tau_stacked_cmp.py base.pkl tau.pkl
"""
from __future__ import annotations

import pickle
import sys
from statistics import median

import numpy as np

BASE, TAU = sys.argv[1], sys.argv[2]
#: 軸信頼ゲート（#565 で `C_hit` も対象になった）を掛けるか。`--no-axis-gate` で外す。
AXIS_GATE = "--no-axis-gate" not in sys.argv
RNG = np.random.default_rng(20260911)
NBOOT = 4000


def load(p):
    return pickle.load(open(p, "rb"))


def sold(rows, win, key=None):
    return {r["i"]: r for r in rows
            if r["win"] == win and r["gate"]
            and (r.get("axis_ok", True) or not AXIS_GATE)
            and (key is None or r["key"] == key)}


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
    return dict(
        n=n, k=sum(r["k"] for r in rs) / n,
        hit=len(hits) / n * 100,
        gami=len(gami) / len(hits) * 100 if hits else 0.0,
        shown=len(shown) / n * 100,
        med_pay=median(pays) if pays else 0.0,
        med_mean=median(sorted(r["mean"] for r in rs)),
        big=sum(1 for p in pays if p >= 100_000),
        roi=sum(r["pay"] for r in rs) / inv * 100 if inv else 0.0,
        swap=sum(1 for r in rs if r["swapped"]) / n * 100,
    )


def boot_delta(pairs):
    """Δ表示的中（tau − base）の点推定と 95%CI。pairs=[(base_shown, tau_shown)]"""
    a = np.array([p[0] for p in pairs], float)
    b = np.array([p[1] for p in pairs], float)
    pt = (b.mean() - a.mean()) * 100
    n = len(a)
    idx = RNG.integers(0, n, size=(NBOOT, n))
    d = (b[idx].mean(1) - a[idx].mean(1)) * 100
    return pt, float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))


def row(name, s, nd):
    if s is None:
        return f"  {name:22s} (該当なし)"
    return (f"  {name:22s} {s['n']/nd:6.2f} {s['k']:6.2f} {s['hit']:7.2f} "
            f"{s['gami']:6.2f} {s['shown']:8.2f} {s['med_pay']:9,.0f} "
            f"{s['med_mean']:10,.0f} {s['big']/nd:8.3f} {s['roi']:7.1f} {s['swap']:7.1f}")


HEAD = ("  {:22s} {:>6s} {:>6s} {:>7s} {:>6s} {:>8s} {:>9s} {:>10s} {:>8s} {:>7s} {:>7s}"
        .format("腕", "件/日", "点数", "的中%", "ガミ%", "表示的中%", "払戻中央",
                "平均払戻中", "10万+/日", "ROI%", "swap%"))


def main() -> None:
    B, T = load(BASE), load(TAU)
    for win, label in (("confirm", "確認 2026-01〜08"), ("explore", "探索 2024-07〜2025-12")):
        ndb = days(B, win)
        print("")
        print("=" * 120)
        print(f"=== {label}  （営業日 {ndb}・軸信頼ゲート {'あり' if AXIS_GATE else 'なし'}）===")
        for scope, key in (("C_hit 単体", "C_hit"), ("ラインナップ全体", None)):
            sb, st = sold(B, win, key), sold(T, win, key)
            common = sorted(set(sb) & set(st))
            print("")
            print(f"-- {scope}  現行 n={len(sb):,} / τ適応 n={len(st):,} / 共通 n={len(common):,} --")
            print(HEAD)
            print(row("現行", summ(list(sb.values())), ndb))
            print(row("τ適応", summ(list(st.values())), ndb))
            print(row("現行(共通のみ)", summ([sb[i] for i in common]), ndb))
            print(row("τ適応(共通のみ)", summ([st[i] for i in common]), ndb))
            pairs = [(sb[i]["pay"] > sb[i]["inv"], st[i]["pay"] > st[i]["inv"])
                     for i in common]
            pt, lo, hi = boot_delta(pairs)
            keep = sum(1 for a, b in pairs if a and b)
            brk = sum(1 for a, b in pairs if a and not b)
            sav = sum(1 for a, b in pairs if b and not a)
            print(f"  Δ表示的中 = {pt:+.2f}pt  95%CI [{lo:+.2f}, {hi:+.2f}]"
                  f"   維持 {keep} / 破壊 {brk} / 救済 {sav}")
            if key == "C_hit":
                kb = [sb[i]["k"] for i in common]
                kt = [st[i]["k"] for i in common]
                import collections
                cb = collections.Counter(kb)
                ct = collections.Counter(kt)
                ks = sorted(set(cb) | set(ct))
                print("  実点数の分布（共通レース）")
                print("    点数 " + " ".join(f"{k:>5d}" for k in ks))
                print("    現行 " + " ".join(f"{cb.get(k,0):>5d}" for k in ks))
                print("    τ   " + " ".join(f"{ct.get(k,0):>5d}" for k in ks))
                print(f"    平均 現行 {np.mean(kb):.2f} / τ {np.mean(kt):.2f}"
                      f"   12点未満 {sum(1 for v in kt if v<12)/len(kt)*100:.1f}%"
                      f" / 12点超 {sum(1 for v in kt if v>12)/len(kt)*100:.1f}%"
                      f"   上限16 {sum(1 for v in kt if v>=16)/len(kt)*100:.1f}%")
                # 差し替えの有無で割る
                for tag, f in (("swap 発動", lambda r: r["swapped"]),
                               ("swap 非発動", lambda r: not r["swapped"])):
                    sub = [i for i in common if f(sb[i])]
                    if not sub:
                        continue
                    pr = [(sb[i]["pay"] > sb[i]["inv"], st[i]["pay"] > st[i]["inv"])
                          for i in sub]
                    p2, l2, h2 = boot_delta(pr)
                    print(f"  [現行側 {tag} n={len(sub):,}] "
                          f"現行 {np.mean([a for a,_ in pr])*100:.2f}% → "
                          f"τ {np.mean([b for _,b in pr])*100:.2f}%  "
                          f"Δ {p2:+.2f} [{l2:+.2f}, {h2:+.2f}]")


if __name__ == "__main__":
    main()
