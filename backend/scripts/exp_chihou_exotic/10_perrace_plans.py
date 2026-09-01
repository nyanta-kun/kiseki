"""レース単位の結果表を作る（市場board基準の各プラン + 型の材料）"""
import pandas as pd, numpy as np, itertools
SP="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki-wt-chihou-upset-model/b8d3a508-4958-4d4b-a9f6-b7e18425c649/scratchpad/"
race=pd.read_pickle(SP+"d.pkl")
wf=pd.read_csv(SP+"wf_nomarket.csv")
wf=wf[wf.win_prob_wf.notna()]
wf["p"]=wf.win_prob_wf/wf.groupby("race_id")["win_prob_wf"].transform("sum")
ru=pd.read_pickle(SP+"runners.pkl").dropna(subset=["win_odds"])
ru["win_odds"]=ru.win_odds.astype(float); ru=ru[ru.win_odds>0]
ru["q"]=(1/ru.win_odds)/(1/ru.win_odds).groupby(ru.race_id).transform("sum")
m=wf.merge(ru[["race_id","horse_number","q"]],on=["race_id","horse_number"],how="inner")
fin=ru[ru.finish_position.isin([1,2,3])]
wset=fin.groupby("race_id")["horse_number"].apply(frozenset)
wtri=fin.sort_values(["race_id","finish_position"]).groupby("race_id")["horse_number"].apply(tuple)
rc=race.join(wset.rename("ws")).join(wtri.rename("wtri"))
rc=rc[rc.ws.map(lambda s:isinstance(s,frozenset) and len(s)==3)]
CAL_TRI=[-0.0422,0.9864,0.0522]  # log10予測払戻(倍) = poly(log10(1/q))
rows=[]
for rid,g in m.groupby("race_id"):
    if rid not in rc.index or len(g)<5: continue
    row=rc.loc[rid]
    nums=g.horse_number.values; q=g.q.values; p=g.p.values
    perms=list(itertools.permutations(range(len(nums)),3)); jj=np.array(perms)
    x,y,z=q[jj[:,0]],q[jj[:,1]],q[jj[:,2]]
    Q=x*(y/(1-x))*(z/(1-x-y))
    combos=list(itertools.combinations(range(len(nums)),3)); ii=np.array(combos)
    a,b,c=q[ii[:,0]],q[ii[:,1]],q[ii[:,2]]
    P=np.zeros(len(combos))
    for u,v,w in itertools.permutations([a,b,c]): P+=u*(v/(1-u))*(w/(1-u-v))
    po=10**np.polyval(CAL_TRI,np.log10(1/np.clip(Q,1e-12,None)))  # 予測払戻(倍) 三連単
    o2=np.argsort(-Q); o1=np.argsort(-P)
    trio6=[frozenset(nums[list(combos[i])]) for i in o1[:6]]
    tri10=[tuple(nums[list(perms[i])]) for i in o2[:10]]
    # 帯プラン: 予測200倍以上から確率上位10点(三連単)
    band=np.where(po>=200)[0]
    bo=band[np.argsort(-Q[band])][:10] if len(band) else np.array([],dtype=int)
    triband=[tuple(nums[list(perms[i])]) for i in bo]
    band_pred=float(np.median(po[bo])) if len(bo) else np.nan
    # 指数と市場の一致
    agree_top1 = int(nums[np.argmax(p)]==nums[np.argmax(q)])
    sp = pd.Series(p).corr(pd.Series(q), method="spearman")
    rows.append(dict(race_id=rid, date=row.date, course=row.course_name, hc=row.head_count,
        ent=row.ent_norm, top3=row.top3_share, q1=row.q1, trio=row.trio, tri=row.tri,
        hit_trio6=int(row.ws in trio6), hit_tri10=int(tuple(row.wtri) in tri10),
        hit_band=int(tuple(row.wtri) in triband), n_band=len(bo), band_pred=band_pred,
        agree=agree_top1, sp=sp, pmax=p.max(), qmax=q.max()))
df=pd.DataFrame(rows); df.to_pickle(SP+"perrace.pkl")
print(len(df), df.date.nunique())
def show(sub,label):
    n=len(sub); nd=sub.date.nunique()
    print(f"{label:28s} R={n:6d} 三連複6点 ROI={sub.trio[sub.hit_trio6==1].sum()/(600*n):.3f} 的中={sub.hit_trio6.mean():.3f} | "
          f"三連単10点 ROI={sub.tri[sub.hit_tri10==1].sum()/(1000*n):.3f} 的中={sub.hit_tri10.mean():.3f} | "
          f"帯200倍+10点 ROI={sub.tri[sub.hit_band==1].sum()/(100*sub.n_band.sum() or 1):.3f} "
          f"的中={sub.hit_band.mean():.3f} 払戻中央={sub.tri[sub.hit_band==1].median():,.0f} 件/日={n/nd:.1f}")
show(df,"全体")
df["entq"]=pd.qcut(df.ent,4,labels=["Q1堅い","Q2","Q3","Q4荒れ"])
for k,s in df.groupby("entq",observed=True): show(s,f"波乱度 {k}")
for k,s in df.groupby("agree"): show(s,f"指数1位=市場1番人気 {k}")
df["spq"]=pd.qcut(df.sp,3,labels=["乖離大","中","一致"])
for k,s in df.groupby("spq",observed=True): show(s,f"指数×市場 順位相関 {k}")
