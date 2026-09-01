"""波乱度×高配当帯 の安定性検証（期間分割・ブートストラップCI・閾値スイープ）"""
import pandas as pd, numpy as np, itertools
SP="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki-wt-chihou-upset-model/b8d3a508-4958-4d4b-a9f6-b7e18425c649/scratchpad/"
race=pd.read_pickle(SP+"d.pkl")
ru=pd.read_pickle(SP+"runners.pkl").dropna(subset=["win_odds"])
ru["win_odds"]=ru.win_odds.astype(float); ru=ru[ru.win_odds>0]
ru["q"]=(1/ru.win_odds)/(1/ru.win_odds).groupby(ru.race_id).transform("sum")
fin=ru[ru.finish_position.isin([1,2,3])]
wtri=fin.sort_values(["race_id","finish_position"]).groupby("race_id")["horse_number"].apply(tuple)
rc=race.join(wtri.rename("wtri"))
rc=rc[rc.wtri.map(lambda t:isinstance(t,tuple) and len(t)==3)]
CAL=[-0.0422,0.9864,0.0522]
TH=[100,200,500,1000]
rows=[]
for rid,g in ru.groupby("race_id"):
    if rid not in rc.index or len(g)<5: continue
    row=rc.loc[rid]
    nums=g.horse_number.values; q=g.q.values
    perms=list(itertools.permutations(range(len(nums)),3)); jj=np.array(perms)
    x,y,z=q[jj[:,0]],q[jj[:,1]],q[jj[:,2]]
    Q=x*(y/(1-x))*(z/(1-x-y))
    po=10**np.polyval(CAL,np.log10(1/np.clip(Q,1e-12,None)))
    rec=dict(race_id=rid,date=row.date,ent=row.ent_norm,tri=row.tri,hc=row.head_count,course=row.course_name)
    for th in TH:
        band=np.where(po>=th)[0]
        bo=band[np.argsort(-Q[band])][:10]
        tris=[tuple(nums[list(perms[i])]) for i in bo]
        rec[f"hit{th}"]=int(tuple(row.wtri) in tris); rec[f"n{th}"]=len(bo)
    rows.append(rec)
df=pd.DataFrame(rows); df.to_pickle(SP+"band.pkl")
df["yr"]=df.date.str[:4]
df["entq"]=pd.qcut(df.ent,5,labels=[1,2,3,4,5])
def roi(s,th):
    inv=100*s[f"n{th}"].sum()
    return s.tri[s[f"hit{th}"]==1].sum()/inv if inv>0 else np.nan
def boot(s,th,B=400):
    idx=np.arange(len(s)); rng=np.random.default_rng(0); out=[]
    tri=s.tri.values; hit=s[f"hit{th}"].values; n=s[f"n{th}"].values
    for _ in range(B):
        b=rng.integers(0,len(s),len(s))
        inv=100*n[b].sum()
        out.append((tri[b]*hit[b]).sum()/inv if inv>0 else np.nan)
    return np.nanpercentile(out,[2.5,97.5])
print("=== 予測払戻の閾値 × 波乱度五分位（三連単 確率上位10点・全期間） ===")
tab=pd.DataFrame({f"{th}倍+":[roi(s,th) for _,s in df.groupby("entq",observed=True)] for th in TH},
                 index=["Q1堅い","Q2","Q3","Q4","Q5荒れ"])
print(tab.round(3).to_string())
print()
for th in [200,500]:
    for q in [1,5]:
        s=df[df.entq==q]
        lo,hi=boot(s,th)
        print(f"th={th} entQ{q}: R={len(s)} 買い目={s[f'n{th}'].sum():,} ROI={roi(s,th):.3f} CI[{lo:.3f},{hi:.3f}] "
              f"的中={s[f'hit{th}'].mean():.4f} 払戻中央={s.tri[s[f'hit{th}']==1].median():,.0f}")
print("\n=== 期間分割（波乱度Q5・閾値別 ROI） ===")
for th in TH:
    r=[]
    for yr,s in df[df.entq==5].groupby("yr"):
        r.append(f"{yr}:{roi(s,th):.3f}(R={len(s)})")
    print(f"  {th}倍+ ", " ".join(r))
print("\n=== 波乱度Q5 × 頭数 ===")
s=df[df.entq==5]
print(pd.DataFrame({"ROI200":[roi(g,200) for _,g in s.groupby(s.hc.clip(8,13))],
                    "R":[len(g) for _,g in s.groupby(s.hc.clip(8,13))]},
                   index=sorted(s.hc.clip(8,13).unique())).round(3).to_string())
