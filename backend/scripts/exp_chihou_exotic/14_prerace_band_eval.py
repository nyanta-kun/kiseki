"""実運用条件（発走6分前オッズで選別）で §4 を組み直す。決済は確定払戻。"""
import pandas as pd, numpy as np, itertools
SP="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki-wt-chihou-upset-model/b8d3a508-4958-4d4b-a9f6-b7e18425c649/scratchpad/"
CAL=[-0.0422,0.9864,0.0522]
race=pd.read_pickle(SP+"d.pkl")           # 払戻・確定オッズ由来の構造
ru=pd.read_pickle(SP+"runners.pkl").dropna(subset=["win_odds"])
ru["win_odds"]=ru.win_odds.astype(float); ru=ru[ru.win_odds>0]
fin=ru[ru.finish_position.isin([1,2,3])].sort_values(["race_id","finish_position"])
wtri=fin.groupby("race_id")["horse_number"].apply(tuple)
rc=race.join(wtri.rename("wtri"))
rc=rc[rc.wtri.map(lambda t:isinstance(t,tuple) and len(t)==3)]

pre=pd.read_pickle(SP+"preodds.pkl")
pre["odds"]=pre.odds.astype(float); pre=pre[pre.odds>0]
pre["q"]=(1/pre.odds)/(1/pre.odds).groupby(pre.race_id).transform("sum")
fin_odds=ru.set_index(["race_id","horse_number"]).win_odds

def board(nums,q,wtri,payout,ths=(100,200,500)):
    perms=list(itertools.permutations(range(len(nums)),3)); jj=np.array(perms)
    x,y,z=q[jj[:,0]],q[jj[:,1]],q[jj[:,2]]
    Q=x*(y/(1-x))*(z/(1-x-y))
    po=10**np.polyval(CAL,np.log10(1/np.clip(Q,1e-12,None)))
    out={}
    for th in ths:
        band=np.where(po>=th)[0]
        bo=band[np.argsort(-Q[band])][:10]
        picks=[tuple(nums[list(perms[i])]) for i in bo]
        out[f"hit{th}"]=int(tuple(wtri) in picks); out[f"n{th}"]=len(bo)
        out[f"picks{th}"]=set(picks)
    ent=-(q*np.log(np.clip(q,1e-12,None))).sum()/np.log(len(q))
    out["ent"]=ent
    return out

rows=[]
for rid,g in pre.groupby("race_id"):
    if rid not in rc.index or len(g)<5: continue
    row=rc.loc[rid]
    nums=g.horse_number.values; q=g.q.values
    a=board(nums,q,row.wtri,row.tri)
    # 対照: 同じレースを確定オッズで選別
    fg=ru[(ru.race_id==rid)&ru.horse_number.isin(nums)]
    if len(fg)!=len(g): continue
    fg=fg.set_index("horse_number").loc[nums]
    qf=(1/fg.win_odds.values); qf=qf/qf.sum()
    b=board(nums,qf,row.wtri,row.tri)
    rec=dict(race_id=rid,date=row.date,tri=row.tri,ent_pre=a["ent"],ent_fin=b["ent"],
             ov200=len(a["picks200"]&b["picks200"]))
    for th in (100,200,500):
        rec[f"pre_hit{th}"]=a[f"hit{th}"]; rec[f"pre_n{th}"]=a[f"n{th}"]
        rec[f"fin_hit{th}"]=b[f"hit{th}"]; rec[f"fin_n{th}"]=b[f"n{th}"]
    rows.append(rec)
df=pd.DataFrame(rows); df.to_pickle(SP+"prerace_eval.pkl")
print("評価レース数", len(df), " 日数", df.date.nunique())
print("波乱度 相関(発走前 vs 確定) %.3f / 五分位一致率 %.1f%%" % (
    df.ent_pre.corr(df.ent_fin),
    (pd.qcut(df.ent_pre,5,labels=False)==pd.qcut(df.ent_fin,5,labels=False)).mean()*100))
print("200倍帯の買い目10点の重なり(中央) %.1f点" % df.ov200.median())
def roi(s,pref,th):
    inv=100*s[f"{pref}_n{th}"].sum()
    return s.tri[s[f"{pref}_hit{th}"]==1].sum()/inv if inv>0 else np.nan
def boot(s,pref,th,B=600):
    rng=np.random.default_rng(0); tri=s.tri.values; hit=s[f"{pref}_hit{th}"].values; n=s[f"{pref}_n{th}"].values
    o=[]
    for _ in range(B):
        b=rng.integers(0,len(s),len(s)); inv=100*n[b].sum()
        o.append((tri[b]*hit[b]).sum()/inv if inv>0 else np.nan)
    return np.nanpercentile(o,[2.5,97.5])
for pref,ecol in [("pre","ent_pre"),("fin","ent_fin")]:
    df[f"q_{pref}"]=pd.qcut(df[ecol],5,labels=[1,2,3,4,5])
print("\n=== 200倍+ 上位10点 ROI（波乱度五分位）===")
tab=pd.DataFrame({
 "発走6分前で選別":[roi(s,"pre",200) for _,s in df.groupby("q_pre",observed=True)],
 "確定オッズで選別":[roi(s,"fin",200) for _,s in df.groupby("q_fin",observed=True)],
 "R(発走前)":[len(s) for _,s in df.groupby("q_pre",observed=True)]},index=["Q1堅い","Q2","Q3","Q4","Q5荒れ"])
print(tab.round(3).to_string())
for th in (100,200,500):
    s=df[df.q_pre==5]; lo,hi=boot(s,"pre",th)
    s2=df[df.q_fin==5]
    print(f"\nQ5 {th}倍+: 発走前 R={len(s)} 買い目={s[f'pre_n{th}'].sum():,} ROI={roi(s,'pre',th):.3f} CI[{lo:.3f},{hi:.3f}] "
          f"的中={s[f'pre_hit{th}'].mean():.4f} 払戻中央={s.tri[s[f'pre_hit{th}']==1].median():,.0f} | 確定選別 ROI={roi(s2,'fin',th):.3f}")
print("\n=== 月別（Q5・200倍+・発走前選別）===")
df["mo"]=df.date.str[:6]
print(pd.DataFrame({"ROI":[roi(s,"pre",200) for _,s in df[df.q_pre==5].groupby("mo")],
                    "R":[len(s) for _,s in df[df.q_pre==5].groupby("mo")],
                    "的中":[s.pre_hit200.sum() for _,s in df[df.q_pre==5].groupby("mo")]},
                   index=sorted(df[df.q_pre==5].mo.unique())).round(3).to_string())
