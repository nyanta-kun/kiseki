#!/usr/bin/env python3
"""軸信頼ゲートの下限を上げると外れは減るか（2026-09-04・ユーザー質問）。

現行は各プランの中で軸信頼（上位2車の3着内率の和）が探索窓 p20 未満なら売らない。
「もっと上げれば外れが減るのでは」を、①プラン内十分位の形 ②閾値の掃引 で見る。

🔴 深く切るほど件数が減るので、**各深さで無作為対照20本**を置く。
🔴 確認窓(2026)が本番相当。探索窓は符号の一致確認にのみ使う。
"""
from __future__ import annotations
import pickle, sys
from collections import defaultdict
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
from typef_racetype import AXIS_GATE_MIN

ROWS = pickle.load(open("/tmp/roi_cutoff_rows.pkl", "rb"))
EX = [r for r in ROWS if r["win"] == "explore"]
CF = [r for r in ROWS if r["win"] == "confirm"]
CUR = sorted(AXIS_GATE_MIN)          # 現在ゲートが掛かっている4プラン
print(f"現在ゲートが掛かるプラン: {CUR}")


def pct(field, q):
    by = defaultdict(list)
    for r in EX:
        by[r["plan"]].append(r[field])
    return {p: float(np.percentile(v, q)) for p, v in by.items()}


def stat(rows, nd):
    if not rows:
        return None
    inv = sum(r["inv"] for r in rows); pay = sum(r["pay"] for r in rows)
    hit = [r for r in rows if r["pay"] > 0]
    shown = [r for r in hit if r["pay"] >= r["inv"]]
    return dict(n=len(rows), perday=len(rows) / nd,
                shown=len(shown) / len(rows) * 100,
                miss=(len(rows) - len(shown)) / nd,          # 表示上の外れ（ガミ含む）
                roi=pay / inv * 100, net=(pay - inv) / nd,
                big=sum(1 for r in hit if r["pay"] >= 100_000) / nd)


# ── ① プラン内十分位の形 ──
print("\n" + "=" * 104)
print("■ プラン内の軸信頼 十分位ごとの成績（現行ゲートが掛かる4プランをまとめて）")
print("   D1 が現行で切っている帯の下半分。単調なら深くする意味があり、D1 だけ悪いなら意味がない。")
print("=" * 104)
for wn, rows in (("確認 2026-01〜08（本番相当）", CF), ("探索 2024-07〜2025-12", EX)):
    nd = len({r["date"] for r in rows})
    sub = [r for r in rows if r["plan"] in CUR]
    # プラン内で十分位へ（探索窓の分位で切る＝本番と同じ作り方）
    edges = {p: [float(np.percentile([x["axis"] for x in EX if x["plan"] == p], q))
                 for q in range(10, 100, 10)] for p in CUR}
    dec = defaultdict(list)
    for r in sub:
        dec[int(np.digitize(r["axis"], edges[r["plan"]]))].append(r)
    print(f"\n  {wn}   （4プラン計 {len(sub):,}商品）")
    print(f"    {'十分位':7s} {'n':>6s} {'表示的中%':>9s} {'ROI%':>7s} {'外れ/日':>7s} {'10万+/日':>8s}")
    for k in range(10):
        s = stat(dec[k], nd)
        if s:
            print(f"    D{k+1:<6d} {s['n']:6,} {s['shown']:9.2f} {s['roi']:7.1f} "
                  f"{s['miss']:7.2f} {s['big']:8.3f}")

# ── ② 閾値の掃引 ──
print("\n" + "=" * 118)
print("■ 下限を上げていくとどうなるか（現行の4プランだけに掛ける・p20 が現行）")
print("=" * 118)
for wn, rows in (("確認 2026-01〜08（本番相当）", CF), ("探索 2024-07〜2025-12", EX)):
    nd = len({r["date"] for r in rows})
    print(f"\n  {wn}")
    print(f"    {'下限':6s} {'件/日':>6s} {'表示的中%':>9s} {'外れ/日':>7s} {'ROI%':>6s} "
          f"{'収支/日':>9s} {'10万+/日':>8s}   {'無作為対照20本（同数）':>28s}")
    for q in (0, 10, 20, 30, 40, 50, 60, 70):
        t = pct("axis", q) if q else None
        keep = rows if q == 0 else [r for r in rows
                                    if r["plan"] not in CUR or r["axis"] >= t[r["plan"]]]
        s = stat(keep, nd)
        ctl = ""
        if q:
            cs, cr = [], []
            for seed in range(20):
                rng = np.random.default_rng(seed)
                pick = rng.choice(len(rows), size=len(keep), replace=False)
                u = stat([rows[j] for j in pick], nd)
                cs.append(u["shown"]); cr.append(u["roi"])
            ctl = (f"表示的中 {sum(s['shown'] > c for c in cs):2d}/20 (中央{np.median(cs):5.2f})"
                   f"  ROI {sum(s['roi'] > c for c in cr):2d}/20 (中央{np.median(cr):5.1f})")
        lab = "無し" if q == 0 else f"p{q}"
        mark = " ←現行" if q == 20 else ""
        print(f"    {lab:6s} {s['perday']:6.2f} {s['shown']:9.2f} {s['miss']:7.2f} {s['roi']:6.1f} "
              f"{s['net']:+9,.0f} {s['big']:8.3f}   {ctl}{mark}")


# ── ③ 軸信頼が段差なら、勾配のある量はあるか ──
print("\n" + "=" * 104)
print("■ 比較: 同じ見方で Σp（買い目の合計的中確率）はどうか（全9プラン・プラン内十分位）")
print("=" * 104)
for wn, rows in (("確認 2026-01〜08（本番相当）", CF), ("探索 2024-07〜2025-12", EX)):
    nd = len({r["date"] for r in rows})
    plans = sorted({r["plan"] for r in rows})
    edges = {p: [float(np.percentile([x["sump"] for x in EX if x["plan"] == p], q))
                 for q in range(10, 100, 10)] for p in plans}
    dec = defaultdict(list)
    for r in rows:
        dec[int(np.digitize(r["sump"], edges[r["plan"]]))].append(r)
    print(f"\n  {wn}")
    print(f"    {'十分位':7s} {'n':>6s} {'表示的中%':>9s} {'ROI%':>7s} {'外れ/日':>7s} {'10万+/日':>8s}")
    for k in range(10):
        s = stat(dec[k], nd)
        if s:
            print(f"    D{k+1:<6d} {s['n']:6,} {s['shown']:9.2f} {s['roi']:7.1f} "
                  f"{s['miss']:7.2f} {s['big']:8.3f}")

print("\n" + "=" * 118)
print("■ Σp の下限を上げていく（現行の軸ゲートは残したまま・全プランに追加）")
print("=" * 118)
AX20 = pct("axis", 20)
for wn, rows in (("確認 2026-01〜08（本番相当）", CF), ("探索 2024-07〜2025-12", EX)):
    nd = len({r["date"] for r in rows})
    base = [r for r in rows if r["plan"] not in CUR or r["axis"] >= AX20[r["plan"]]]
    print(f"\n  {wn}")
    print(f"    {'Σp下限':7s} {'件/日':>6s} {'表示的中%':>9s} {'外れ/日':>7s} {'ROI%':>6s} "
          f"{'収支/日':>9s} {'10万+/日':>8s}   {'無作為対照20本（現行から同数を削る）':>30s}")
    for q in (0, 10, 20, 30, 40, 50):
        t = pct("sump", q) if q else None
        keep = base if q == 0 else [r for r in base if r["sump"] >= t[r["plan"]]]
        s = stat(keep, nd)
        ctl = ""
        if q:
            cs, cr = [], []
            for seed in range(20):
                rng = np.random.default_rng(seed)
                pick = rng.choice(len(base), size=len(keep), replace=False)
                u = stat([base[j] for j in pick], nd)
                cs.append(u["shown"]); cr.append(u["roi"])
            ctl = (f"表示的中 {sum(s['shown'] > c for c in cs):2d}/20 (中央{np.median(cs):5.2f})"
                   f"  ROI {sum(s['roi'] > c for c in cr):2d}/20 (中央{np.median(cr):5.1f})")
        lab = "無し" if q == 0 else f"p{q}"
        print(f"    {lab:7s} {s['perday']:6.2f} {s['shown']:9.2f} {s['miss']:7.2f} {s['roi']:6.1f} "
              f"{s['net']:+9,.0f} {s['big']:8.3f}   {ctl}")
