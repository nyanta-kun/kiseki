import pandas as pd, numpy as np
SP="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki-wt-chihou-upset-model/b8d3a508-4958-4d4b-a9f6-b7e18425c649/scratchpad/"
ex = pd.read_pickle(SP+"exotic.pkl")
ru = pd.read_pickle(SP+"runners.pkl")
ru = ru.dropna(subset=["win_odds"])
ru["win_odds"]=ru.win_odds.astype(float)
ru = ru[ru.win_odds>0]
# market implied probs per race
ru["inv"]=1.0/ru.win_odds
g = ru.groupby("race_id")["inv"]
ru["q"]=ru.inv/g.transform("sum")
book = g.sum().rename("book")
# race-level market structure
srt = ru.sort_values(["race_id","q"], ascending=[True,False])
srt["rk"]=srt.groupby("race_id").cumcount()+1
piv = srt[srt.rk<=4].pivot_table(index="race_id",columns="rk",values="q")
piv.columns=[f"q{c}" for c in piv.columns]
ent = ru.groupby("race_id")["q"].apply(lambda s: -(s*np.log(s)).sum())
n_run = ru.groupby("race_id").size().rename("n_run")
race = ex.set_index("race_id").join([piv, ent.rename("entropy"), n_run, book])
race["ent_norm"]=race.entropy/np.log(race.n_run)
race["top3_share"]=race[["q1","q2","q3"]].sum(axis=1)
race["date"]=race["date"].astype(str)
race["yr"]=race.date.str[:4]
print("races", len(race), "book median", race.book.median().round(3))
print(race[["q1","q2","top3_share","ent_norm","n_run","head_count"]].describe().round(3).to_string())

# --- box strategies from market popularity ---
ex2 = race.dropna(subset=["p1","p2","p3","trio","tri"]).copy()
for c in ["p1","p2","p3"]: ex2[c]=ex2[c].astype(float)
ex2["maxpop"]=ex2[["p1","p2","p3"]].max(axis=1)
ndays = ex2.date.nunique()
from math import comb
rows=[]
for N in [3,4,5,6,7]:
    pts = comb(N,3)
    hit = ex2.maxpop<=N
    inv = pts*100*len(ex2)
    ret = ex2.loc[hit,"trio"].sum()
    rows.append(dict(rule=f"trio box top{N}", pts=pts, n=len(ex2), hit=hit.mean(),
                     roi=ret/inv, med_pay=ex2.loc[hit,"trio"].median(), perday=len(ex2)/ndays))
    # trifecta box
    ptsT = N*(N-1)*(N-2)
    invT = ptsT*100*len(ex2)
    retT = ex2.loc[hit,"tri"].sum()
    rows.append(dict(rule=f"trifecta box top{N}", pts=ptsT, n=len(ex2), hit=hit.mean(),
                     roi=retT/invT, med_pay=ex2.loc[hit,"tri"].median(), perday=len(ex2)/ndays))
print()
print(pd.DataFrame(rows).round(4).to_string(index=False))
race.to_pickle(SP+"race.pkl")
