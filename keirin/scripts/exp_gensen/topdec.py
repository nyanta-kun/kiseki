#!/usr/bin/env python3
"""PL top1確率の上位帯で「614が選ぶ/選ばない」の差が本物かを日次ブートストラップで見る。"""
from __future__ import annotations
import itertools, json, os, sys
from pathlib import Path
import numpy as np
REPO = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
CANON = list(itertools.permutations(range(1, 8), 3)); BUDGET = 10_000
z = np.load("/tmp/honmei_attr.npz", allow_pickle=True)
KEY = list(z["KEY"]); PROB, WIN, PAY, OK, DATE = z["PROB"], z["WIN"], z["PAY"], z["OKPRED"], z["DATE"]
sel = {json.loads(l)["race_key"] for l in (Path(__file__).resolve().parent / "gensen" / "joined.jsonl").open(encoding="utf-8")}
ii = np.array([i for i in range(len(KEY)) if "2026-01-01" <= DATE[i] <= "2026-08-26" and OK[i]
               and int(WIN[i]) >= 0 and np.isfinite(PAY[i])])
S = np.array([KEY[i] in sel for i in ii])
top1 = PROB[ii].max(1)
hit = np.array([CANON[int(WIN[i])] == CANON[int(np.argmax(PROB[i]))] for i in ii])
pay1 = np.where(hit, PAY[ii] * BUDGET / 100.0, 0.0)
day = np.array([str(DATE[i]) for i in ii])
q = np.quantile(top1, [0.9])
for name, m in (("上位10%", top1 >= q[0]), ("全体", np.ones(len(ii), bool))):
    a, b = m & S, m & ~S
    rng = np.random.default_rng(0); days = sorted(set(day)); dh = []; dr = []
    for _ in range(3000):
        ds = set(rng.choice(days, len(days), replace=True))
        # 日単位リサンプル（重複は1回として扱わず、出現回数で重み付け）
        w = np.zeros(len(ii))
        cnt = {}
        for d in rng.choice(days, len(days), replace=True): cnt[d] = cnt.get(d, 0) + 1
        for k2, v in cnt.items(): w[day == k2] = v
        wa, wb = w * a, w * b
        if wa.sum() == 0 or wb.sum() == 0: continue
        dh.append((hit * wa).sum() / wa.sum() - (hit * wb).sum() / wb.sum())
        dr.append((pay1 * wa).sum() / (wa.sum() * BUDGET) - (pay1 * wb).sum() / (wb.sum() * BUDGET))
    print(f"[{name}] 選出 n={a.sum()} 的中 {hit[a].mean()*100:.2f}% ROI {pay1[a].sum()/(a.sum()*BUDGET)*100:.1f}%"
          f" | 非選出 n={b.sum()} 的中 {hit[b].mean()*100:.2f}% ROI {pay1[b].sum()/(b.sum()*BUDGET)*100:.1f}%")
    print(f"   差: 的中 {np.mean(dh)*100:+.2f}pt CI[{np.percentile(dh,2.5)*100:+.2f},{np.percentile(dh,97.5)*100:+.2f}]"
          f"   ROI {np.mean(dr)*100:+.1f}pt CI[{np.percentile(dr,2.5)*100:+.1f},{np.percentile(dr,97.5)*100:+.1f}]")
