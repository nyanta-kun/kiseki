import pickle
import statistics
from collections import defaultdict
from pathlib import Path

import numpy as np

R = pickle.load(open(Path(__file__).resolve().parent / "ae_rows.pkl","rb")) + pickle.load(open(Path(__file__).resolve().parent / "ae_rows_bc.pkl","rb"))
R = [r for r in R if r["used"] in ("A_hit","B_hit","C_hit")]
ND = {"探索":549,"確認":216}
def res(r,a): return (r["inv"],r["pay"]) if a=="cur" else ((r["arms"][a] or (r["inv"],r["pay"],0))[:2])
def met(rs,acts,w):
    v=[res(r,a) for r,a in zip(rs,acts)]; h=[p for i,p in v if p>i]
    return dict(s=100*len(h)/len(v), roi=100*sum(p for _,p in v)/sum(i for i,_ in v), med=statistics.median(h), b4=sum(p>=40000 for p in h)/ND[w], b2=sum(p>=20000 for p in h)/ND[w])
def boot(rs,acts,B=800):
    days=defaultdict(list)
    for j,r in enumerate(rs): days[r["day"]].append(j)
    ks=list(days); rng=np.random.default_rng(0); ds=[]; dr=[]
    for _ in range(B):
        idx=[j for k in rng.integers(0,len(ks),len(ks)) for j in days[ks[k]]]
        a=met([rs[j] for j in idx],[acts[j] for j in idx],"確認"); b=met([rs[j] for j in idx],["cur"]*len(idx),"確認")
        ds.append(a["s"]-b["s"]); dr.append(a["roi"]-b["roi"])
    return np.percentile(ds,[2.5,97.5]), np.percentile(dr,[2.5,97.5])
thr={p: {q: np.quantile([r["f"]["pc5"] for r in R if r["used"]==p and r["win"]=="探索"], q) for q in (0.5,0.7)} for p in ("A_hit","B_hit","C_hit")}
for grp in (("B_hit",),("C_hit",),("A_hit","B_hit","C_hit")):
    for w in ("探索","確認"):
        rs=[r for r in R if r["win"]==w and r["used"] in grp]
        print(f"\n{'+'.join(grp)} {w} n={len(rs)}")
        P=[("現行5万",["cur"]*len(rs))]+[(f"一律{a}",[a]*len(rs)) for a in ("T40k","T30k")]
        for q in (0.7,0.5):
            for T in ("T25k","T20k"):
                P.append((f"pc5上位{int(round(100-100*q))}%→{T}",[T if r["f"]["pc5"]>=thr[r["used"]][q] else "cur" for r in rs]))
        for nm,acts in P:
            m=met(rs,acts,w)
            extra=""
            if nm!="現行5万":
                (a,b),(c,d)=boot(rs,acts)
                rc=sum(1 for r,x in zip(rs,acts) if x!="cur" and res(r,x)[1]>res(r,x)[0] and not r["pay"]>r["inv"])
                br=sum(1 for r,x in zip(rs,acts) if x!="cur" and r["pay"]>r["inv"] and not res(r,x)[1]>res(r,x)[0])
                extra=f"  Δ的中[{a:+.1f},{b:+.1f}] ΔROI[{c:+.1f},{d:+.1f}] 救{rc}/壊{br}"
            print(f"  {nm:<18} 的中{m['s']:6.2f} ROI{m['roi']:6.1f} 払戻中央{m['med']:>7,.0f} 2万+/日{m['b2']:5.2f} 4万+/日{m['b4']:6.3f}{extra}")
