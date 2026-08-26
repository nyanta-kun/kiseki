#!/usr/bin/env python3
"""614 選出の的中リフトが窓をまたいで再現するか（2026 H1 / H2 分割）。"""
from __future__ import annotations
import itertools, json, os, sys
from pathlib import Path
import numpy as np
REPO = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
CANON = list(itertools.permutations(range(1, 8), 3)); BUDGET = 10_000
z = np.load("/tmp/honmei_attr.npz", allow_pickle=True)
KEY = list(z["KEY"]); PROB, WIN, PAY, OK, DATE = z["PROB"], z["WIN"], z["PAY"], z["OKPRED"], z["DATE"]
sel = {json.loads(l)["race_key"] for l in (Path(__file__).resolve().parent/"gensen"/"joined.jsonl").open(encoding="utf-8")}

def block(lo, hi, label):
    ii = np.array([i for i in range(len(KEY)) if lo <= DATE[i] <= hi and OK[i]
                   and int(WIN[i]) >= 0 and np.isfinite(PAY[i])])
    S = np.array([KEY[i] in sel for i in ii]); top1 = PROB[ii].max(1)
    hit = np.array([int(WIN[i]) == int(np.argmax(PROB[i])) for i in ii])
    pay1 = np.where(hit, PAY[ii] * BUDGET / 100.0, 0.0)
    day = np.array([str(DATE[i]) for i in ii]); days = sorted(set(day))
    q90 = np.quantile(top1, 0.9)
    print(f"\n[{label}] n={len(ii):,}  選出 {S.sum():,}")
    for nm, m in (("全体", np.ones(len(ii), bool)), ("PLtop1上位10%", top1 >= q90)):
        a, b = m & S, m & ~S
        rng = np.random.default_rng(1); dh = []; dr = []
        for _ in range(2000):
            cnt = {}
            for d in rng.choice(days, len(days), replace=True): cnt[d] = cnt.get(d, 0) + 1
            w = np.zeros(len(ii))
            for k2, v in cnt.items(): w[day == k2] = v
            wa, wb = w * a, w * b
            if wa.sum() == 0 or wb.sum() == 0: continue
            dh.append((hit*wa).sum()/wa.sum() - (hit*wb).sum()/wb.sum())
            dr.append((pay1*wa).sum()/(wa.sum()*BUDGET) - (pay1*wb).sum()/(wb.sum()*BUDGET))
        print(f"  {nm:14s} 選出 n={a.sum():4d} 的中 {hit[a].mean()*100:5.2f}% ROI {pay1[a].sum()/(a.sum()*BUDGET)*100:6.1f}%"
              f" | 非 n={b.sum():4d} 的中 {hit[b].mean()*100:5.2f}% ROI {pay1[b].sum()/(b.sum()*BUDGET)*100:6.1f}%"
              f" | Δ的中 {np.mean(dh)*100:+5.2f}pt[{np.percentile(dh,2.5)*100:+5.2f},{np.percentile(dh,97.5)*100:+5.2f}]"
              f" ΔROI {np.mean(dr)*100:+5.1f}pt[{np.percentile(dr,2.5)*100:+5.1f},{np.percentile(dr,97.5)*100:+5.1f}]")

block("2026-01-01", "2026-04-30", "2026 H1")
block("2026-05-01", "2026-08-26", "2026 H2")
