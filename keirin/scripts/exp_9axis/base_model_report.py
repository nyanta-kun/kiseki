import numpy as np, pandas as pd
R = pd.read_pickle("/tmp/ab9.pkl")
ARMS = ["A0 現行","A1 車数対応","A2 9車専用","A3 9車専用+"]
rng = np.random.default_rng(11)

def inrace_auc(d, col):
    num=den=0.0
    for rk,g in d.groupby("race_key", sort=False):
        y=g.top3.values; s=g[col].values
        pos=s[y==1]; neg=s[y==0]
        if not len(pos) or not len(neg): continue
        num+=(pos[:,None]>neg[None,:]).sum()+0.5*(pos[:,None]==neg[None,:]).sum()
        den+=len(pos)*len(neg)
    return num/den

print(f"検定 9車 {R.race_key.nunique():,}R\n")
print("=== レース内 concordance（3着内の識別力）===")
for a in ARMS: print(f"  {a:12s} {inrace_auc(R,a):.4f}")

def pair_stats(col):
    out=[]
    for rk,g in R.groupby("race_key", sort=False):
        o=g.sort_values(col, ascending=False)
        a1=o.iloc[0]; a2=o.iloc[1]
        out.append((rk, int(a1.top3), int(a1.top3)*int(a2.top3)))
    return pd.DataFrame(out, columns=["rk","a1","both"]).set_index("rk")

S={a:pair_stats(a) for a in ARMS}
print("\n=== 軸1 3着内 / 二軸そろい ===")
base=S["A0 現行"]
for a in ARMS:
    s=S[a]
    line=f"  {a:12s} 軸1 {s.a1.mean()*100:5.2f}%  二軸 {s.both.mean()*100:5.2f}%"
    if a!="A0 現行":
        d=(s.both-base.both).values
        b=[d[rng.integers(0,len(d),len(d))].mean()*100 for _ in range(3000)]
        line+=f"  Δ二軸 {d.mean()*100:+5.2f}pt CI[{np.percentile(b,2.5):+.2f},{np.percentile(b,97.5):+.2f}]"
    print(line)

print("\n=== 較正（予測平均 − 実測）===")
for a in ARMS:
    print(f"  {a:12s} 予測 {R[a].mean():.4f} 実測 {R.top3.mean():.4f} 乖離 {100*(R[a].mean()-R.top3.mean()):+.2f}pt "
          f"Σ/レース {R.groupby('race_key')[a].sum().mean():.3f}")

print("\n=== 窓別 二軸そろい ===")
R["q"]=pd.to_datetime(R.race_date).dt.to_period("Q").astype(str)
qm={rk:q for rk,q in zip(R.race_key,R.q)}
tab={}
for a in ARMS:
    s=S[a].copy(); s["q"]=[qm[i] for i in s.index]
    tab[a]=s.groupby("q").both.mean()*100
print(pd.DataFrame(tab).round(2))
