"""市場のみ(Harville)で確率上位k点を買った場合の商品指標。型ラボの物差しで測る。"""
import pandas as pd, numpy as np, itertools
SP="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki-wt-chihou-upset-model/b8d3a508-4958-4d4b-a9f6-b7e18425c649/scratchpad/"
race = pd.read_pickle(SP+"d.pkl")
ru = pd.read_pickle(SP+"runners.pkl").dropna(subset=["win_odds"])
ru["win_odds"]=ru.win_odds.astype(float); ru=ru[ru.win_odds>0]
ru["q"]=(1/ru.win_odds)/(1/ru.win_odds).groupby(ru.race_id).transform("sum")
qm={rid:(g.horse_number.values,g.q.values) for rid,g in ru.groupby("race_id")}
fin=ru[ru.finish_position.isin([1,2,3])]
wset=fin.groupby("race_id")["horse_number"].apply(frozenset)
race=race.join(wset.rename("ws"))
race=race[race.ws.map(lambda s: isinstance(s,frozenset) and len(s)==3)]
ndays=race.date.nunique()
KS=[1,2,3,6,10]
res={k:dict(hit=0,ret=0.0,pays=[],gami=0,n=0) for k in KS}
by_ent={ (k,q):dict(hit=0,ret=0.0,n=0) for k in KS for q in [1,2,3,4]}
race["ent_q4"]=pd.qcut(race.ent_norm,4,labels=[1,2,3,4]).astype(int)
for rid,row in race.iterrows():
    if rid not in qm: continue
    nums,q=qm[rid]
    if len(nums)<4: continue
    combos=list(itertools.combinations(range(len(nums)),3))
    a=np.array([q[c[0]] for c in combos]); b=np.array([q[c[1]] for c in combos]); c3=np.array([q[c[2]] for c in combos])
    P=np.zeros(len(combos))
    for x,y,z in itertools.permutations([a,b,c3]):
        P+=x*(y/(1-x))*(z/(1-x-y))
    order=np.argsort(-P)
    sets=[frozenset(nums[list(combos[i])]) for i in order[:max(KS)]]
    for k in KS:
        r=res[k]; r["n"]+=1
        e=by_ent[(k,row.ent_q4)]; e["n"]+=1
        if row.ws in sets[:k]:
            r["hit"]+=1; r["ret"]+=row.trio; r["pays"].append(row.trio)
            if row.trio<=100*k: r["gami"]+=1
            e["hit"]+=1; e["ret"]+=row.trio
out=[]
for k in KS:
    r=res[k]
    out.append(dict(rule=f"三連複 確率上位{k}点(市場)", 点数=k, R数=r["n"], 件日=round(r["n"]/ndays,1),
        的中率=r["hit"]/r["n"], ガミ率=r["gami"]/max(r["hit"],1),
        表示的中=(r["hit"]-r["gami"])/r["n"], 払戻中央=np.median(r["pays"]),
        ROI=r["ret"]/(100*k*r["n"])))
print(pd.DataFrame(out).round(4).to_string(index=False))
print("\n=== 波乱度四分位別 ROI（三連複 確率上位k点・市場） ===")
tab=pd.DataFrame({f"top{k}":[by_ent[(k,q)]["ret"]/(100*k*max(by_ent[(k,q)]["n"],1)) for q in [1,2,3,4]] for k in KS},
                 index=["Q1堅い","Q2","Q3","Q4荒れ"])
print(tab.round(3).to_string())
print("\n=== 波乱度四分位別 的中率 ===")
tab2=pd.DataFrame({f"top{k}":[by_ent[(k,q)]["hit"]/max(by_ent[(k,q)]["n"],1) for q in [1,2,3,4]] for k in KS},
                 index=["Q1堅い","Q2","Q3","Q4荒れ"])
print(tab2.round(3).to_string())
