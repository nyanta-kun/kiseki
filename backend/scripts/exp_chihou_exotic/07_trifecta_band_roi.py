"""三連単の帯別ROI（市場PL価格で帯分け）"""
import pandas as pd, numpy as np, itertools
SP="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki-wt-chihou-upset-model/b8d3a508-4958-4d4b-a9f6-b7e18425c649/scratchpad/"
race=pd.read_pickle(SP+"d.pkl")
ru=pd.read_pickle(SP+"runners.pkl").dropna(subset=["win_odds"])
ru["win_odds"]=ru.win_odds.astype(float); ru=ru[ru.win_odds>0]
ru["q"]=(1/ru.win_odds)/(1/ru.win_odds).groupby(ru.race_id).transform("sum")
qm={rid:(g.horse_number.values,g.q.values) for rid,g in ru.groupby("race_id")}
fin=ru[ru.finish_position.isin([1,2,3])].sort_values(["race_id","finish_position"])
tri=fin.groupby("race_id")["horse_number"].apply(tuple)
race=race.join(tri.rename("wtri"))
race=race[race.wtri.map(lambda t: isinstance(t,tuple) and len(t)==3)]
edges=np.array([0,3e-5,1e-4,3e-4,1e-3,3e-3,1e-2,3e-2,1.0]); nb=len(edges)-1
bought=np.zeros(nb); ret=np.zeros(nb); hits=np.zeros(nb); pays=[[] for _ in range(nb)]
for rid,row in race.iterrows():
    if rid not in qm: continue
    nums,q=qm[rid]; k=len(nums)
    if k<4: continue
    idx=list(itertools.permutations(range(k),3))
    ii=np.array(idx)
    a=q[ii[:,0]]; b=q[ii[:,1]]; c=q[ii[:,2]]
    P=a*(b/(1-a))*(c/(1-a-b))
    bi=np.clip(np.digitize(P,edges)-1,0,nb-1)
    np.add.at(bought,bi,1)
    key=tuple(row.wtri)
    pos={h:j for j,h in enumerate(nums)}
    if all(h in pos for h in key):
        tgt=(pos[key[0]],pos[key[1]],pos[key[2]])
        j=idx.index(tgt); wb=bi[j]
        hits[wb]+=1; ret[wb]+=row.tri; pays[wb].append(row.tri)
lab=[f"{edges[i]:.0e}-{edges[i+1]:.0e}" for i in range(nb)]
print(pd.DataFrame(dict(band=lab, 想定オッズ=[f"{0.72/((edges[i]+edges[i+1])/2):,.0f}" for i in range(nb)],
  買い目数=bought.astype(int), 的中=hits.astype(int),
  ROI=np.where(bought>0,ret/(100*np.maximum(bought,1)),np.nan),
  払戻中央=[np.median(p) if p else np.nan for p in pays])).round(3).to_string(index=False))
