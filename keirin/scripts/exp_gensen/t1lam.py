#!/usr/bin/env python3
"""7T1 の組み立てに『同ライン隣接ボーナス λ』を入れたときの効果（vintage板・2026のみ）。
7T1 は予測オッズ（train_end 2025-12-31）を使うので 2026 だけを読む。"""
from __future__ import annotations
import itertools, os, sys
from pathlib import Path
from statistics import median
import numpy as np
REPO = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
from src.strategy_wt import (_rank_7t1_min_odds, RANK_7T1_TARGET_PAYOUT,  # noqa: E402
                             RANK_7T1_KMAX, rank_7t1_stakes)
CANON = list(itertools.permutations(range(1, 8), 3)); CIDX = {c: j for j, c in enumerate(CANON)}
BUDGET, UNIT = 10_000, 100
z = np.load("/tmp/honmei_attr.npz", allow_pickle=True)
PROB, PO, WIN, PAY, OK, DATE = z["PROB"], z["PO"], z["WIN"], z["PAY"], z["OKPRED"], z["DATE"]
P3, LG, LPOS, RTYPE = z["P3"], z["LG"], z["A_line_pos"], z["RTYPE"]

def select(i, lam, target=RANK_7T1_TARGET_PAYOUT, axis1_top_n=2):
    order = list(np.argsort(-P3[i]) + 1)
    allow = set(int(x) for x in order[:axis1_top_n])
    best = None
    for a1 in order:
        a1 = int(a1)
        if a1 not in allow: continue
        for a2 in order:
            a2 = int(a2)
            if a2 == a1: continue
            adj = (LG[i][a1-1] == LG[i][a2-1] and LG[i][a1-1] not in ("", "0")
                   and LPOS[i][a2-1] == LPOS[i][a1-1] + 1)
            bonus = lam if adj else 1.0
            sc = sorted(((PROB[i][CIDX[(a1, a2, c)]], PO[i][CIDX[(a1, a2, c)]], (a1, a2, c))
                         for c in range(1, 8) if c not in (a1, a2)), key=lambda t: -t[0])
            for k in range(1, min(RANK_7T1_KMAX, len(sc)) + 1):
                bar = _rank_7t1_min_odds(k, target, BUDGET, UNIT)
                feas = [t for t in sc if t[1] >= bar]
                if len(feas) < k: break
                obj = sum(t[0] for t in feas[:k]) * bonus
                if best is None or obj > best[0]:
                    best = (obj, [t[2] for t in feas[:k]])
    return None if best is None else best[1]

base = np.flatnonzero(OK & (WIN >= 0) & np.isfinite(PAY) & (DATE >= "2026-01-01"))
for pop_name, popmask in (("全7車", None), ("決勝系", ("決勝", "チャレンジ決勝"))):
    idxs = [i for i in base if popmask is None or RTYPE[i] in popmask]
    print(f"\n[{pop_name}] n={len(idxs):,}")
    for lam in (1.0, 1.5, 2.0, 2.5, 3.0):
        out = []
        for i in idxs:
            legs = select(i, lam)
            if not legs: continue
            st = rank_7t1_stakes(["-".join(map(str, l)) for l in legs])
            pp = {tuple(int(x) for x in kk.split("-")): v for kk, v in st.items()}
            w = CANON[int(WIN[i])]
            out.append((sum(pp.values()), PAY[i] * pp.get(w, 0) / 100.0, len(pp), str(DATE[i])))
        nd = len(set(o[3] for o in out)); inv = sum(o[0] for o in out); pay = sum(o[1] for o in out)
        hs = sorted(o[1] for o in out if o[1] > 0)
        print(f"  λ={lam:4.1f} {len(out)/nd:6.2f}件/日 点{np.mean([o[2] for o in out]):4.2f} "
              f"的中 {len(hs)/len(out)*100:5.2f}% ROI {pay/inv*100:6.1f}% 中央 {median(hs) if hs else 0:>8,.0f} "
              f"15万+{sum(1 for p in hs if p>=150_000)/nd:6.3f}/日 30万+{sum(1 for p in hs if p>=300_000)/nd:6.3f}/日")
