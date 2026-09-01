"""指数(honest walk-forward)と市場で三連複/三連単の並べ替えを比較する。"""
import pandas as pd, numpy as np, itertools, sys
SP="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki-wt-chihou-upset-model/b8d3a508-4958-4d4b-a9f6-b7e18425c649/scratchpad/"
race=pd.read_pickle(SP+"d.pkl")
wf=pd.read_csv(SP+"wf_nomarket.csv")
print("wf:", wf.shape, wf.race_id.nunique(), wf.date.min(), wf.date.max())
wf=wf[wf.win_prob_wf.notna()]
wf["p"]=wf.win_prob_wf/wf.groupby("race_id")["win_prob_wf"].transform("sum")
ru=pd.read_pickle(SP+"runners.pkl").dropna(subset=["win_odds"])
ru["win_odds"]=ru.win_odds.astype(float); ru=ru[ru.win_odds>0]
ru["q"]=(1/ru.win_odds)/(1/ru.win_odds).groupby(ru.race_id).transform("sum")
m=wf.merge(ru[["race_id","horse_number","q","finish_position"]],on=["race_id","horse_number"],how="inner",suffixes=("","_r"))
# blend (対数プール)
for lam in [0.3,0.5,0.7]:
    m[f"b{lam}"]=np.exp(lam*np.log(m.p.clip(1e-6))+(1-lam)*np.log(m.q.clip(1e-6)))
    m[f"b{lam}"]=m[f"b{lam}"]/m.groupby("race_id")[f"b{lam}"].transform("sum")
fin=ru[ru.finish_position.isin([1,2,3])]
wset=fin.groupby("race_id")["horse_number"].apply(frozenset)
tri=ru[ru.finish_position.isin([1,2,3])].sort_values(["race_id","finish_position"]).groupby("race_id")["horse_number"].apply(tuple)
rc=race.join(wset.rename("ws")).join(tri.rename("wtri"))
rc=rc[rc.ws.map(lambda s:isinstance(s,frozenset) and len(s)==3)]
cols=["q","p","b0.3","b0.5","b0.7"]
KS=[3,6,10]
acc={(c,k,bt):dict(n=0,hit=0,ret=0.0,pays=[]) for c in cols for k in KS for bt in ["trio","tri"]}
rc["yr"]=rc.date.str[:4]
grp={rid:g for rid,g in m.groupby("race_id")}
nrace=0
for rid,row in rc.iterrows():
    g=grp.get(rid)
    if g is None or len(g)<4: continue
    nums=g.horse_number.values
    combos=list(itertools.combinations(range(len(nums)),3))
    perms=list(itertools.permutations(range(len(nums)),3))
    ii=np.array(combos); jj=np.array(perms)
    for c in cols:
        pv=g[c].values
        a,b,c3=pv[ii[:,0]],pv[ii[:,1]],pv[ii[:,2]]
        P=np.zeros(len(combos))
        for x,y,z in itertools.permutations([a,b,c3]):
            P+=x*(y/(1-x))*(z/(1-x-y))
        o=np.argsort(-P)
        sets=[frozenset(nums[list(combos[i])]) for i in o[:max(KS)]]
        x2,y2,z2=pv[jj[:,0]],pv[jj[:,1]],pv[jj[:,2]]
        Q=x2*(y2/(1-x2))*(z2/(1-x2-y2))
        o2=np.argsort(-Q)
        tris=[tuple(nums[list(perms[i])]) for i in o2[:max(KS)]]
        for k in KS:
            A=acc[(c,k,"trio")]; A["n"]+=1
            if row.ws in sets[:k]: A["hit"]+=1; A["ret"]+=row.trio; A["pays"].append(row.trio)
            B=acc[(c,k,"tri")]; B["n"]+=1
            if tuple(row.wtri) in tris[:k]: B["hit"]+=1; B["ret"]+=row.tri; B["pays"].append(row.tri)
    nrace+=1
print("races evaluated:", nrace)
rows=[]
for bt in ["trio","tri"]:
    for k in KS:
        for c in cols:
            A=acc[(c,k,bt)]
            rows.append(dict(券種=bt, 点数=k, 確率=c, n=A["n"], 的中率=A["hit"]/max(A["n"],1),
                             ROI=A["ret"]/(100*k*max(A["n"],1)), 払戻中央=np.median(A["pays"]) if A["pays"] else np.nan))
print(pd.DataFrame(rows).round(4).to_string(index=False))
