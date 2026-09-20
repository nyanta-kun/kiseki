import sys, numpy as np, pandas as pd, itertools
from sklearn.metrics import roc_auc_score, log_loss
sys.path.insert(0,"/Users/ysuzuki/GitHub/kiseki/keirin")
from src.strategy_wt import rank_7t3_blend_probs
OUT="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/C_model"
CACHE="/Users/ysuzuki/GitHub/kiseki/keirin/data/feature_cache/wtfeat_20221201_20260831_f70_728566_20260831.pkl"

samp=pd.read_csv(f"{OUT}/sample_races.csv"); keys=set(samp.race_key)
m=pd.read_pickle(f"{OUT}/wf_preds_audit.pkl")
m=m[m.race_key.isin(keys)].copy()
meta=pd.read_pickle(CACHE)[["race_key","frame_no","line_group","line_pos","grade","race_type"]]
m=m.merge(meta,on=["race_key","frame_no"],how="left")
od=pd.read_pickle(f"{OUT}/odds_trifecta_sample.pkl")
od=od[od.odds_value>0].copy()
print("races", m.race_key.nunique(), "entries", len(m), "odds rows", len(od))

od[["c1","c2","c3"]]=od.combination.str.split("-",expand=True).astype(int)
od["inv"]=1.0/od.odds_value
od["tot"]=od.groupby("race_key")["inv"].transform("sum")
od["pm"]=od.inv/od.tot
# 市場の周辺確率
mk_win=od.groupby(["race_key","c1"])["pm"].sum().rename("mkt_pw")
long=pd.concat([od[["race_key","pm"]].assign(car=od.c1),
                od[["race_key","pm"]].assign(car=od.c2),
                od[["race_key","pm"]].assign(car=od.c3)])
mk_p3=long.groupby(["race_key","car"])["pm"].sum().rename("mkt_p3")
m=m.merge(mk_win.reset_index().rename(columns={"c1":"frame_no"}),on=["race_key","frame_no"],how="left")
m=m.merge(mk_p3.reset_index().rename(columns={"car":"frame_no"}),on=["race_key","frame_no"],how="left")
m=m.dropna(subset=["mkt_pw","mkt_p3"])
# モデル側もレース内で正規化（pw→Σ1 / p3→Σ3）
m["mdl_pw"]=m.pw/m.groupby("race_key")["pw"].transform("sum")
m["mdl_p3"]=3.0*m.p3/m.groupby("race_key")["p3"].transform("sum")
m["mdl_p3"]=m.mdl_p3.clip(1e-6,1-1e-6); m["mkt_p3"]=m.mkt_p3.clip(1e-6,1-1e-6)
print("takeout(1/Σ1/O):", (1.0/od.groupby("race_key")["inv"].first().index.size) if False else round(float((od.groupby("race_key")["tot"].first()).mean()),4))
m.to_pickle(f"{OUT}/market_merged.pkl")

def rep(name,d):
    r={"seg":name,"n_race":d.race_key.nunique()}
    for tag,(mc,kc,y) in {"win":("mdl_pw","mkt_pw","win_flag"),
                          "top3":("mdl_p3","mkt_p3","top3_flag")}.items():
        r[f"auc_mdl_{tag}"]=roc_auc_score(d[y],d[mc]); r[f"auc_mkt_{tag}"]=roc_auc_score(d[y],d[kc])
        r[f"ll_mdl_{tag}"]=log_loss(d[y],d[mc].clip(1e-6,1-1e-6))
        r[f"ll_mkt_{tag}"]=log_loss(d[y],d[kc].clip(1e-6,1-1e-6))
    return r
rows=[rep("ALL",m)]
for y,d in m.groupby(m.race_date.str[:4]): rows.append(rep(f"year {y}",d))
print(pd.DataFrame(rows).to_string(index=False,float_format=lambda x:f"{x:.4f}"))

# 1番人気 vs モデル1位
g=m.copy()
g["r_mdl"]=g.groupby("race_key")["mdl_pw"].rank(ascending=False,method="first")
g["r_mkt"]=g.groupby("race_key")["mkt_pw"].rank(ascending=False,method="first")
g["r_mdl3"]=g.groupby("race_key")["mdl_p3"].rank(ascending=False,method="first")
g["r_mkt3"]=g.groupby("race_key")["mkt_p3"].rank(ascending=False,method="first")
print("\n=== 1位馬（車）の実績 ===")
print("model pw1  : win %.4f top3 %.4f n=%d"%(g[g.r_mdl==1].win_flag.mean(),g[g.r_mdl==1].top3_flag.mean(),(g.r_mdl==1).sum()))
print("market fav : win %.4f top3 %.4f n=%d"%(g[g.r_mkt==1].win_flag.mean(),g[g.r_mkt==1].top3_flag.mean(),(g.r_mkt==1).sum()))
print("agree rate (model pw1 == market fav): %.4f"%(g[(g.r_mdl==1)&(g.r_mkt==1)].shape[0]/g.race_key.nunique()))
# 二軸そろい
for nm,col in (("model p3top2","r_mdl3"),("market p3top2","r_mkt3")):
    t=g[g[col]<=2].groupby("race_key")["top3_flag"].sum()
    print(f"{nm}: 二軸そろい {(t==2).mean():.4f}  n={len(t)}")
# 食い違い時
d=g[(g.r_mdl==1)]
sp=d.groupby(d.r_mkt.clip(upper=4)).agg(n=("win_flag","size"),win=("win_flag","mean"),top3=("top3_flag","mean"))
print("\nモデル1位が市場で何番人気か × 実績\n",sp.to_string(float_format=lambda x:f"{x:.4f}"))
d2=g[(g.r_mkt==1)]
sp2=d2.groupby(d2.r_mdl.clip(upper=4)).agg(n=("win_flag","size"),win=("win_flag","mean"),top3=("top3_flag","mean"))
print("\n市場1番人気がモデルで何位か × 実績\n",sp2.to_string(float_format=lambda x:f"{x:.4f}"))
