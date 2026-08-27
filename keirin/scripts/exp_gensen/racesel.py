#!/usr/bin/env python3
"""614 の「厳選」の中身 — 何を見てレースを選んでいるのか、そこに妙味はあるのか。"""
from __future__ import annotations
import itertools, json, os, sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO)); os.chdir(REPO)
CANON = list(itertools.permutations(range(1, 8), 3))
BUDGET = 10_000
z = np.load("/tmp/honmei_attr.npz", allow_pickle=True)
KEY = list(z["KEY"]); PROB, PO, WIN, PAY = z["PROB"], z["PO"], z["WIN"], z["PAY"]
P3, PW, OK, DATE, RTYPE, GRADE = z["P3"], z["PW"], z["OKPRED"], z["DATE"], z["RTYPE"], z["GRADE"]

sel = set()
for l in (Path(__file__).resolve().parent / "gensen" / "joined.jsonl").open(encoding="utf-8"):
    sel.add(json.loads(l)["race_key"])

ii = np.array([i for i in range(len(KEY)) if "2026-01-01" <= DATE[i] <= "2026-08-26" and OK[i]
               and int(WIN[i]) >= 0 and np.isfinite(PAY[i])])
S = np.array([KEY[i] in sel for i in ii])
print(f"母集団 {len(ii):,}R  うち614選出 {S.sum():,} ({S.mean()*100:.1f}%)")

top1 = PROB[ii].max(1)
pw_sorted = -np.sort(-PW[ii], 1)
p3_sorted = -np.sort(-P3[ii], 1)
feats = {
    "PL top1確率": top1,
    "pw 1位": pw_sorted[:, 0],
    "pw 1位-2位差": pw_sorted[:, 0] - pw_sorted[:, 1],
    "p3 1位": p3_sorted[:, 0],
    "Σp3上位3": p3_sorted[:, :3].sum(1),
    "PL top1 予測オッズ": np.array([PO[i][np.argmax(PROB[i])] for i in ii]),
}
print("\n[選出・非選出の分布差]")
for nm, v in feats.items():
    a, b = v[S], v[~S]
    print(f"  {nm:18s} 選出 {np.mean(a):8.4f} (中央{np.median(a):8.4f}) / 非選出 {np.mean(b):8.4f}"
          f" (中央{np.median(b):8.4f})")

print("\n[レース種別ごとの選出率]")
rt = RTYPE[ii]
for t, c in Counter(rt).most_common(14):
    m = rt == t
    print(f"  {t:14s} n={c:5d}  選出率 {S[m].mean()*100:5.1f}%")
print("[級別]")
for t, c in Counter(GRADE[ii]).most_common():
    m = GRADE[ii] == t
    print(f"  {t:6s} n={c:5d}  選出率 {S[m].mean()*100:5.1f}%")

# PL top1 確率の十分位で層別し、同じ帯の中で 614 選出/非選出の PL top1・1点成績を比べる
print("\n[PL top1確率 十分位 × 614選出 — 同じ帯の中で妙味差があるか]")
q = np.quantile(top1, np.linspace(0, 1, 11))
pay1 = np.array([PAY[i] * BUDGET / 100.0 if CANON[int(WIN[i])] ==
                 CANON[int(np.argmax(PROB[i]))] else 0.0 for i in ii])
print("  帯   top1確率     n(選)  的中(選)  ROI(選) |  n(非)  的中(非)  ROI(非)")
for k in range(10):
    m = (top1 >= q[k]) & (top1 <= q[k + 1] if k == 9 else top1 < q[k + 1])
    for lbl, mm in (("", m & S), ("", m & ~S)):
        pass
    a, b = m & S, m & ~S
    def st(mask):
        if mask.sum() == 0: return (0, 0.0, 0.0)
        p = pay1[mask]
        return (mask.sum(), (p > 0).mean() * 100, p.sum() / (mask.sum() * BUDGET) * 100)
    na, ha, ra = st(a); nb, hb, rb = st(b)
    print(f"  {k+1:2d}  {q[k]:.3f}-{q[k+1]:.3f}  {na:5d}  {ha:6.2f}%  {ra:6.1f}% | {nb:5d}  {hb:6.2f}%  {rb:6.1f}%")
