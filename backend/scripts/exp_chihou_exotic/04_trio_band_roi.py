"""全三連複の組を市場(単勝オッズ→Harville)で値付けし、帯ごとの実測ROIを出す。"""
import pandas as pd, numpy as np, itertools, sys
SP="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki-wt-chihou-upset-model/b8d3a508-4958-4d4b-a9f6-b7e18425c649/scratchpad/"
race = pd.read_pickle(SP+"race.pkl")
ru = pd.read_pickle(SP+"runners.pkl").dropna(subset=["win_odds"])
ru["win_odds"]=ru.win_odds.astype(float); ru=ru[ru.win_odds>0]
ru["inv"]=1/ru.win_odds
ru["q"]=ru.inv/ru.groupby("race_id")["inv"].transform("sum")
# 的中組（1-3着の馬番）
fin = ru[ru.finish_position.isin([1,2,3])][["race_id","horse_number","finish_position"]]
win3 = fin.groupby("race_id")["horse_number"].apply(lambda s: frozenset(s)).rename("wset")
win3n = fin.groupby("race_id").size().rename("nfin")
race = race.join(win3).join(win3n)
race = race[(race.nfin==3) & race.trio.notna()]
qmap = {rid: (g.horse_number.values, g.q.values) for rid,g in ru.groupby("race_id")}

edges = np.array([0,1e-4,3e-4,1e-3,3e-3,1e-2,3e-2,1e-1,1.0])
nb=len(edges)-1
bought=np.zeros(nb); ret=np.zeros(nb); hits=np.zeros(nb); pays=[[] for _ in range(nb)]
# 型別（波乱度四分位）にも分ける
race["ent_q"]=pd.qcut(race.ent_norm,4,labels=[1,2,3,4]).astype(int)
bought_t=np.zeros((4,nb)); ret_t=np.zeros((4,nb)); hits_t=np.zeros((4,nb))
n=0
for rid, row in race.iterrows():
    if rid not in qmap: continue
    nums,q = qmap[rid]
    k=len(nums)
    if k<4: continue
    idx=np.arange(k)
    combos=np.array(list(itertools.combinations(idx,3)))
    a,b,c = q[combos[:,0]],q[combos[:,1]],q[combos[:,2]]
    # Harville: sum over 6 orders
    P=np.zeros(len(combos))
    for x,y,z in itertools.permutations([a,b,c]):
        P += x*(y/(1-x))*(z/(1-x-y))
    bi=np.clip(np.digitize(P,edges)-1,0,nb-1)
    np.add.at(bought,bi,1)
    t=row.ent_q-1
    np.add.at(bought_t[t],bi,1)
    ws=row.wset
    win_mask = np.array([set(nums[list(cc)])==set(ws) for cc in combos])
    if win_mask.any():
        j=int(np.argmax(win_mask)); wb=bi[j]
        hits[wb]+=1; ret[wb]+=row.trio; pays[wb].append(row.trio)
        hits_t[t][wb]+=1; ret_t[t][wb]+=row.trio
    n+=1
print("races used", n)
lab=[f"{edges[i]:.0e}-{edges[i+1]:.0e}" for i in range(nb)]
df=pd.DataFrame(dict(band=lab, 想定オッズ=[f"{0.745/((edges[i]+edges[i+1])/2):,.0f}" for i in range(nb)],
                     買い目数=bought.astype(int), 的中=hits.astype(int),
                     的中率=np.where(bought>0,hits/np.maximum(bought,1),np.nan),
                     ROI=np.where(bought>0,ret/(100*np.maximum(bought,1)),np.nan),
                     払戻中央=[np.median(p) if p else np.nan for p in pays]))
print(df.to_string(index=False))
print()
print("=== 波乱度四分位 × 帯 の ROI ===")
roi_t=pd.DataFrame(np.where(bought_t>0, ret_t/(100*np.maximum(bought_t,1)), np.nan), columns=lab,
                   index=["ent Q1(堅い)","Q2","Q3","Q4(荒れ)"])
print(roi_t.round(3).to_string())
print()
print("=== 帯ごとの買い目シェア(全体) ===")
print((bought/bought.sum()).round(4))
