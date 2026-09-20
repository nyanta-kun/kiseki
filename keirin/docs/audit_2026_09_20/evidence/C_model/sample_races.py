import sys, pandas as pd, numpy as np
sys.path.insert(0,"/Users/ysuzuki/GitHub/kiseki/keirin")
CACHE="/Users/ysuzuki/GitHub/kiseki/keirin/data/feature_cache/wtfeat_20221201_20260831_f70_728566_20260831.pkl"
OUT="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/C_model"
df=pd.read_pickle(CACHE)
df["race_date"]=df["race_date"].astype(str)
d=df[(df.race_date>="2024-07-01")&(df.race_date<="2026-08-31")]
g=d.groupby("race_key").agg(n=("frame_no","size"), date=("race_date","first"),
                            nfin=("finish_order", lambda s: (s>=1).sum()),
                            nnull=("finish_order", lambda s: s.isna().sum()))
ok=g[(g.n==7)&(g.nnull==0)&(g.nfin==7)]   # 7車・全員確定・DNFなし（着順の一意性を確保）
print("7car all-finished races:", len(ok))
ok=ok.copy(); ok["ym"]=ok["date"].str[:7]
rng=np.random.default_rng(20260920)
samp=[]
for ym,grp in ok.groupby("ym"):
    k=min(160,len(grp))
    samp.append(grp.sample(k,random_state=int(ym.replace("-",""))))
s=pd.concat(samp)
print("sampled:", len(s), "months:", s.ym.nunique())
s.reset_index().to_csv(f"{OUT}/sample_races.csv", index=False)
