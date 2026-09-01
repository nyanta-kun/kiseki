"""発走6分前オッズでの型分けを、別の切り口(top3_share / 1番人気)でも確認する。"""
import pandas as pd, numpy as np, itertools
SP="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki-wt-chihou-upset-model/b8d3a508-4958-4d4b-a9f6-b7e18425c649/scratchpad/"
CAL=[-0.0422,0.9864,0.0522]
race=pd.read_pickle(SP+"d.pkl")
ru=pd.read_pickle(SP+"runners.pkl").dropna(subset=["win_odds"])
ru["win_odds"]=ru.win_odds.astype(float); ru=ru[ru.win_odds>0]
fin=ru[ru.finish_position.isin([1,2,3])].sort_values(["race_id","finish_position"])
rc=race.join(fin.groupby("race_id")["horse_number"].apply(tuple).rename("wtri"))
rc=rc[rc.wtri.map(lambda t:isinstance(t,tuple) and len(t)==3)]
pre=pd.read_pickle(SP+"preodds.pkl"); pre["odds"]=pre.odds.astype(float); pre=pre[pre.odds>0]
pre["q"]=(1/pre.odds)/(1/pre.odds).groupby(pre.race_id).transform("sum")
rows=[]
for rid,g in pre.groupby("race_id"):
    if rid not in rc.index or len(g)<5: continue
    row=rc.loc[rid]; nums=g.horse_number.values; q=g.q.values
    perms=list(itertools.permutations(range(len(nums)),3)); jj=np.array(perms)
    x,y,z=q[jj[:,0]],q[jj[:,1]],q[jj[:,2]]
    Q=x*(y/(1-x))*(z/(1-x-y)); po=10**np.polyval(CAL,np.log10(1/np.clip(Q,1e-12,None)))
    rec=dict(race_id=rid,date=row.date,tri=row.tri,hc=len(nums),
             ent=-(q*np.log(np.clip(q,1e-12,None))).sum()/np.log(len(q)),
             top3=np.sort(q)[::-1][:3].sum(), q1=q.max())
    for th in (100,200):
        band=np.where(po>=th)[0]; bo=band[np.argsort(-Q[band])][:10]
        picks=[tuple(nums[list(perms[i])]) for i in bo]
        rec[f"hit{th}"]=int(tuple(row.wtri) in picks); rec[f"n{th}"]=len(bo)
    rows.append(rec)
df=pd.DataFrame(rows)
def roi(s,th): 
    inv=100*s[f"n{th}"].sum(); return s.tri[s[f"hit{th}"]==1].sum()/inv if inv>0 else np.nan
print("n=",len(df))
for col,asc in [("ent",True),("top3",False),("q1",False)]:
    df["b"]=pd.qcut(df[col],5,labels=[1,2,3,4,5])
    r100=[roi(s,100) for _,s in df.groupby("b",observed=True)]
    r200=[roi(s,200) for _,s in df.groupby("b",observed=True)]
    lab={"ent":"波乱度(発走前)","top3":"上位3頭シェア(小さいほど荒れ)","q1":"1番人気の含意勝率"}[col]
    print(f"\n{lab}  五分位(小→大)")
    print("  100倍+:", " ".join(f"{v:.3f}" for v in r100))
    print("  200倍+:", " ".join(f"{v:.3f}" for v in r200))
print("\n全体 100倍+ ROI %.3f / 200倍+ ROI %.3f （発走前選別・%d日）"%(roi(df,100),roi(df,200),df.date.nunique()))
