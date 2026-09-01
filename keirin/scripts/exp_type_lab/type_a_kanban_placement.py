#!/usr/bin/env python3
"""看板案を「A_hit が売れないレースだけ」に置けば共食いしないか（2026-08-31）。

型A のうち A_hit が入稿ゲートで落ちるレース（＝いま無商品）に限って
飛び三連複／飛び三連単を出したときの看板本数と ROI を測る。
"""
import sys, itertools, random
from collections import defaultdict
from pathlib import Path
from statistics import median
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np, type_a_upset2 as M

def _trio_rank(d):
    cars=sorted(d["o"][1:])
    pr={frozenset(c):sum(float(d["PROB"][M.CIDX[p]]) for p in itertools.permutations(c))
        for c in itertools.combinations(cars,3)}
    return sorted(pr,key=lambda k:-pr[k])
def _tf_rank(d):
    cars=set(d["o"][1:])
    idx=[i for i,t in enumerate(M.CANON) if set(t)<=cars]
    idx.sort(key=lambda i:-float(d["PROB"][i]))
    return [M.CANON[i] for i in idx]
for k in (3,5):
    M.ARMS[f"飛び三連複{k}点"]=(lambda k: lambda d:("trio",_trio_rank(d)[:k]))(k)
    M.ARMS[f"飛び三連単{k}点"]=(lambda k: lambda d:("tf",_tf_rank(d)[:k]))(k)

data=M.load()
for win,(lo,hi) in M.WINDOWS.items():
    rs=[d for d in data if lo<=d["date"]<=hi]
    nd=len({d["date"] for d in rs})
    sold=[d for d in rs if M.play(d,"A_hit 現行3点")]
    unsold=[d for d in rs if not M.play(d,"A_hit 現行3点")]
    print(f"\n{'='*112}\n=== {win}  型A {len(rs):,}R / {nd}日 ===")
    print(f"  A_hit が入稿ゲートを通る {len(sold):,}R ({len(sold)/nd:.2f}件/日) / "
          f"通らない（＝いま無商品）{len(unsold):,}R ({len(unsold)/nd:.2f}件/日)")
    b_s=sum(1 for d in sold if d["o"][0] not in d["f"])/max(len(sold),1)
    b_u=sum(1 for d in unsold if d["o"][0] not in d["f"])/max(len(unsold),1)
    print(f"  軸崩壊率  売れている側 {b_s:.1%} ↔ 無商品側 {b_u:.1%}")
    print(f"\n  {'母集団':<22}{'腕':<14}{'件/日':>7}{'表示的中':>9}{'払戻中央':>10}"
          f"{'10万+本':>8}{'10万+/日':>10}{'30万+本':>8}{'ROI(CI)':>18}")
    for lab, sub in (("いま無商品のレースだけ", unsold), ("A_hit が売れているレース", sold),
                     ("型A 全部", rs)):
        for a in ("飛び三連複3点","飛び三連単5点"):
            recs=[r for r in (M.play(d,a) for d in sub) if r]
            if not recs: continue
            inv=sum(r["inv"] for r in recs); pay=sum(r["pay"] for r in recs)
            h=[r for r in recs if r["pay"]>0]; sh=[r for r in h if r["pay"]>r["inv"]]
            ps=sorted(r["pay"] for r in h)
            big=[x for x in ps if x>=100_000]; hug=[x for x in ps if x>=300_000]
            l_,h_=M.boot_roi(recs)
            print(f"  {lab:<22}{a:<14}{len(recs)/nd:>7.2f}{len(sh)/len(recs)*100:>8.2f}%"
                  f"{(median(ps) if ps else 0):>10,.0f}{len(big):>8}{len(big)/nd:>10.3f}"
                  f"{len(hug):>8}  {pay/inv*100:>6.1f}[{l_:.0f},{h_:.0f}]")
