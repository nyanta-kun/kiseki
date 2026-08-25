import numpy as np, pandas as pd
D = pd.read_pickle("/tmp/diag9.pkl")
MARK = {"◎":5,"○":4,"▲":3,"△":2,"×":1}
D["markv"] = D["mark"].map(lambda x: MARK.get(x,0) if x else 0)
D["rp"] = pd.to_numeric(D.rp, errors="coerce")

def inrace_auc(d, col):
    """レース内の pairwise concordance（3着内 vs 着外）。"""
    num=den=0.0
    for rk,g in d.groupby("rk", sort=False):
        y=g.top3.values; s=g[col].values
        pos=s[y==1]; neg=s[y==0]
        if not len(pos) or not len(neg): continue
        c=(pos[:,None]>neg[None,:]).sum()+0.5*(pos[:,None]==neg[None,:]).sum()
        num+=c; den+=len(pos)*len(neg)
    return num/den

print("=== レース内 pairwise concordance（3着内の識別力）===")
print(f"{'':10s} {'7車':>8s} {'9車':>8s}")
for col,lab in (("p3","モデルp3"),("pw","モデルpw"),("rp","競走得点"),("markv","公式印")):
    v7=inrace_auc(D[D.nc==7], col); v9=inrace_auc(D[D.nc==9], col)
    print(f"{lab:10s} {v7:8.4f} {v9:8.4f}")

print("\n=== 二軸（p3上位2車）そろい率 ===")
for nc in (7,9):
    d=D[D.nc==nc]
    a=d[d.p3rank<=2].groupby("rk").top3.sum()
    rand = {7:3/7*2/6, 9:3/9*2/8}[nc]
    print(f"  {nc}車 n={len(a):,}  そろい {(a==2).mean()*100:.2f}%  ランダム {rand*100:.2f}%  比 {(a==2).mean()/rand:.2f}x")

print("\n=== 軸1(p3 1位)/軸2(p3 2位) 個別 ===")
for nc in (7,9):
    d=D[D.nc==nc]
    for r in (1,2,3):
        x=d[d.p3rank==r]
        print(f"  {nc}車 p3順位{r}: 3着内 {x.top3.mean()*100:5.2f}%  1着 {x.win.mean()*100:5.2f}%")

print("\n=== 9車: 種別・グレード別の二軸そろい ===")
d=D[(D.nc==9)&(D.p3rank<=2)]
g=d.groupby(["rtype","rk"]).top3.sum().reset_index()
t=g.groupby("rtype").agg(R=("top3","size"), soroi=("top3", lambda s:(s==2).mean()*100)).sort_values("R",ascending=False)
print(t.round(2).head(12))
