#!/usr/bin/env python3
"""足切りの追検証: 落とした側のROI / ゲートの組み合わせ / プラン別の向き（2026-09-04）。"""
from __future__ import annotations
import pickle, sys
from collections import defaultdict
from statistics import median
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts" / "exp_type_lab"))
from typef_racetype import AXIS_GATE_MIN

ROWS = pickle.load(open("/tmp/roi_cutoff_rows.pkl", "rb"))
EX = [r for r in ROWS if r["win"] == "explore"]
CF = [r for r in ROWS if r["win"] == "confirm"]
CUR = set(AXIS_GATE_MIN)

def thr(field, q):
    by = defaultdict(list)
    for r in EX:
        by[r["plan"]].append(r[field])
    return {p: float(np.percentile(v, q)) for p, v in by.items()}

def st(rows, nd):
    if not rows:
        return None
    inv = sum(r["inv"] for r in rows); pay = sum(r["pay"] for r in rows)
    hits = [r for r in rows if r["pay"] > 0]
    shown = [r for r in hits if r["pay"] >= r["inv"]]
    byday = defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        byday[r["date"]][0] += r["inv"]; byday[r["date"]][1] += r["pay"]
    d = sorted(p / i * 100 for i, p in byday.values() if i > 0)
    return dict(n=len(rows), perday=len(rows)/nd, shown=len(shown)/len(rows)*100,
                roi=pay/inv*100, net=(pay-inv)/nd, inv=inv/nd,
                lose=sum(1 for x in d if x < 100)/len(d)*100,
                med=median(d), big=sum(1 for r in hits if r["pay"] >= 100_000)/nd)

AX20, AX40 = thr("axis", 20), thr("axis", 40)
SP20 = thr("sump", 20)

def gate(rows, ax=None, ax_plans=None, sp=None):
    out = []
    for r in rows:
        if ax is not None and (ax_plans is None or r["plan"] in ax_plans) \
           and r["axis"] < ax.get(r["plan"], 0.0):
            continue
        if sp is not None and r["sump"] < sp.get(r["plan"], 0.0):
            continue
        out.append(r)
    return out

ARMS = {
    "0 足切り無し": lambda rows: rows,
    "1 現行（軸p20・4プラン）": lambda rows: gate(rows, AX20, CUR),
    "2 現行4プランを p40 へ": lambda rows: gate(rows, AX40, CUR),
    "3 軸 全プラン p20": lambda rows: gate(rows, AX20),
    "4 軸 全プラン p40": lambda rows: gate(rows, AX40),
    "5 現行 + Σp全プラン p20": lambda rows: gate(rows, AX20, CUR, SP20),
    "6 軸全p40 + Σp全p20": lambda rows: gate(rows, AX40, None, SP20),
}

for wn, rows in (("確認 2026-01〜08（本番相当）", CF), ("探索 2024-07〜2025-12", EX)):
    nd = len({r["date"] for r in rows})
    print("\n" + "=" * 112)
    print(f"■ {wn}  — 売る側 と 落とした側 を並べる")
    print("=" * 112)
    print(f"  {'腕':24s} {'件/日':>6s} {'売る:表示的中%':>13s} {'ROI%':>6s} │ "
          f"{'落とす件/日':>10s} {'落とす:表示的中%':>15s} {'ROI%':>6s} │ {'収支/日':>9s} {'負け日%':>7s} {'10万+/日':>8s}")
    for name, fn in ARMS.items():
        keep = fn(rows)
        ks = {id(r) for r in keep}
        drop = [r for r in rows if id(r) not in ks]
        a, b = st(keep, nd), st(drop, nd)
        bs = (f"{b['perday']:10.2f} {b['shown']:15.2f} {b['roi']:6.1f}" if b
              else f"{0:10.2f} {'-':>15s} {'-':>6s}")
        print(f"  {name:24s} {a['perday']:6.2f} {a['shown']:13.2f} {a['roi']:6.1f} │ {bs} │ "
              f"{a['net']:+9,.0f} {a['lose']:7.1f} {a['big']:8.3f}")

print("\n" + "=" * 112)
print("■ 収支/日の改善は「ROIが上がったから」か「賭ける額が減ったから」か（確認窓）")
print("=" * 112)
nd = len({r["date"] for r in CF})
b0 = st(CF, nd)
print(f"  {'腕':24s} {'投資/日':>9s} {'収支/日':>9s} {'改善額':>9s} "
      f"{'うち額を減らした分':>16s} {'うちROIが上がった分':>18s}")
for name, fn in ARMS.items():
    s = st(fn(CF), nd)
    imp = s["net"] - b0["net"]
    vol = (b0["roi"] / 100 - 1) * (s["inv"] - b0["inv"])      # ROI据置で額だけ動かした分
    print(f"  {name:24s} {s['inv']:9,.0f} {s['net']:+9,.0f} {imp:+9,.0f} "
          f"{vol:+16,.0f} {imp - vol:+18,.0f}")

print("\n" + "=" * 112)
print("■ プラン別の向き（売る − 落とす の ROI・両窓で同符号か）")
print("=" * 112)
for gname, ax, plans in (("軸信頼 p20", AX20, None), ("軸信頼 p40", AX40, None)):
    print(f"\n  ── {gname} 全プランに掛けた場合 ──")
    print(f"    {'プラン':8s} {'探索: 売るROI':>12s} {'落とすROI':>10s} {'差':>7s}   "
          f"{'確認: 売るROI':>12s} {'落とすROI':>10s} {'差':>7s}  両窓同符号")
    for p in sorted({r["plan"] for r in ROWS}):
        cells, signs = [], []
        for rows in (EX, CF):
            sub = [r for r in rows if r["plan"] == p]
            k = [r for r in sub if r["axis"] >= ax.get(p, 0.0)]
            d = [r for r in sub if r["axis"] < ax.get(p, 0.0)]
            if not k or not d:
                cells.append(f"{'-':>12s} {'-':>10s} {'-':>7s}"); signs.append(0); continue
            a, b = st(k, 1), st(d, 1)
            cells.append(f"{a['roi']:12.1f} {b['roi']:10.1f} {a['roi']-b['roi']:+7.1f}")
            signs.append(np.sign(a["roi"] - b["roi"]))
        ok = "○" if signs[0] == signs[1] != 0 else "×"
        print(f"    {p:8s} {cells[0]}   {cells[1]}  {ok}")
