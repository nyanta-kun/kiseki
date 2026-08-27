#!/usr/bin/env python3
"""三連単「大きいところ」の運用点メニュー（vintage板・2026・H1/H2で安定性を見る）。

各構成 = 母集団 × 予測オッズ下限 × 点数k。1レース10,000円をk等分。
⚠️ 予測オッズモデル odds_tf_n7 の train_end = 2025-12-31 なので 2026 のみを読む。
"""
from __future__ import annotations
import itertools, os, sys
from pathlib import Path
from statistics import median
import numpy as np
REPO = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
CANON = list(itertools.permutations(range(1, 8), 3)); BUDGET, UNIT = 10_000, 100
z = np.load("/tmp/honmei_attr.npz", allow_pickle=True)
PROB, PO, WIN, PAY, OK, DATE, RTYPE = z["PROB"], z["PO"], z["WIN"], z["PAY"], z["OKPRED"], z["DATE"], z["RTYPE"]

GROUPS = {
    "全7車": None,
    "決勝系(決/チ決)": ("決勝", "チャレンジ決勝"),
    "勝ち上がり(予選/準決/特一般)": ("予選", "準決勝", "特一般", "チャレンジ予選", "チャレンジ準決勝", "特予選"),
    "決勝+準決勝": ("決勝", "チャレンジ決勝", "準決勝", "チャレンジ準決勝"),
}

def run(mask, minodds, k):
    out = []
    for i in np.flatnonzero(mask):
        c = np.flatnonzero(PO[i] >= minodds)
        if len(c) < k: continue
        c = c[np.argsort(-PROB[i][c])][:k]
        s = BUDGET // k // UNIT * UNIT
        inv = s * k
        pay = PAY[i] * s / 100.0 if int(WIN[i]) in set(int(x) for x in c) else 0.0
        out.append((inv, pay, str(DATE[i])))
    return out

def stat(out):
    if not out: return None
    nd = len(set(o[2] for o in out))
    inv = sum(o[0] for o in out); pay = sum(o[1] for o in out)
    hits = sorted(o[1] for o in out if o[1] > 0)
    return dict(n=len(out), perday=len(out)/nd, hit=len(hits)/len(out)*100,
                roi=pay/inv*100, med=median(hits) if hits else 0,
                b10=sum(1 for p in hits if p >= 100_000)/nd,
                b30=sum(1 for p in hits if p >= 300_000)/nd,
                b100=sum(1 for p in hits if p >= 1_000_000)/nd)

base = OK & (WIN >= 0) & np.isfinite(PAY)
W = {"H1": (DATE >= "2026-01-01") & (DATE <= "2026-04-30"),
     "H2": (DATE >= "2026-05-01") & (DATE <= "2026-08-26"),
     "通": (DATE >= "2026-01-01") & (DATE <= "2026-08-26")}
print("母集団 / 帯 / 点数    件/日  的中   ROI  払戻中央  10万+/日 30万+/日 100万+/日   (H1 ROI / H2 ROI)")
for gname, types in GROUPS.items():
    tm = np.ones(len(PROB), bool) if types is None else np.isin(RTYPE, types)
    for minodds in (0, 15, 30, 50, 100):
        for k in (1, 2, 3, 5):
            s = stat(run(base & tm & W["通"], minodds, k))
            if not s or s["perday"] < 0.3: continue
            h1 = stat(run(base & tm & W["H1"], minodds, k))
            h2 = stat(run(base & tm & W["H2"], minodds, k))
            print(f"{gname:26s} {minodds:3d}倍+ {k}点 {s['perday']:6.2f} {s['hit']:5.2f}% {s['roi']:6.1f}%"
                  f" {s['med']:>9,.0f} {s['b10']:8.3f} {s['b30']:8.3f} {s['b100']:9.4f}"
                  f"   ({h1['roi']:5.1f} / {h2['roi']:5.1f})")
    print()
