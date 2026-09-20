import sys, numpy as np, pandas as pd
from sklearn.metrics import roc_auc_score, log_loss
sys.path.insert(0,"/Users/ysuzuki/GitHub/kiseki/keirin")
OUT="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/C_model"
CACHE="/Users/ysuzuki/GitHub/kiseki/keirin/data/feature_cache/wtfeat_20221201_20260831_f70_728566_20260831.pkl"
m=pd.read_pickle(f"{OUT}/wf_preds_audit.pkl")
meta=pd.read_pickle(CACHE)[["race_key","frame_no","grade","race_type","venue_id"]]
m=m.merge(meta,on=["race_key","frame_no"],how="left")
m["n_car"]=m.groupby("race_key")["frame_no"].transform("size")
m["year"]=m.race_date.str[:4]
m["q"]=m.window
def f(x): return f"{x:.4f}"

rows=[]
def blk(name, d):
    if len(d)<500: return
    r={"seg":name,"n_entry":len(d),"n_race":d.race_key.nunique()}
    r["auc_p3"]=roc_auc_score(d.top3_flag,d.p3); r["auc_pw"]=roc_auc_score(d.win_flag,d.pw)
    r["ll_p3"]=log_loss(d.top3_flag,d.p3.clip(1e-6,1-1e-6))
    # レース内順位
    g=d.copy()
    g["r3"]=g.groupby("race_key")["p3"].rank(ascending=False,method="first")
    g["rw"]=g.groupby("race_key")["pw"].rank(ascending=False,method="first")
    top1w=g[g.rw==1]; top1p3=g[g.r3==1]
    r["pw1_win"]=top1w.win_flag.mean(); r["pw1_top3"]=top1w.top3_flag.mean()
    r["p31_win"]=top1p3.win_flag.mean(); r["p31_top3"]=top1p3.top3_flag.mean()
    # 二軸そろい（p3 上位2車が両方3着内）
    t2=g[g.r3<=2].groupby("race_key")["top3_flag"].sum()
    r["axis2_both"]=(t2==2).mean(); r["n_race2"]=len(t2)
    rows.append(r)

blk("ALL", m)
for y,d in m.groupby("year"): blk(f"year {y}", d)
for q,d in m.groupby("q"): blk(f"win {q}", d)
for n,d in m.groupby("n_car"):
    if len(d)>2000: blk(f"n_car {n}", d)
m7=m[m.n_car==7]
blk("n7 ALL", m7)
for y,d in m7.groupby("year"): blk(f"n7 {y}", d)
for gname,d in m7.groupby("grade"): blk(f"n7 grade {gname}", d)
for rt,d in m7.groupby("race_type"):
    if len(d)>3000: blk(f"n7 type {rt}", d)
df=pd.DataFrame(rows)
pd.set_option("display.width",250)
print(df.to_string(index=False, float_format=lambda x:f"{x:.4f}"))
df.to_csv(f"{OUT}/an1_discrim.csv",index=False)

# 較正（十分位）
print("\n=== 較正 十分位（全体・p3） ===")
for col,tgt in (("p3","top3_flag"),("pw","win_flag")):
    d=m.copy(); d["dec"]=pd.qcut(d[col],10,labels=False,duplicates="drop")
    t=d.groupby("dec").agg(n=(col,"size"),pred=(col,"mean"),obs=(tgt,"mean"))
    t["diff"]=t.obs-t.pred
    print(f"--- {col} ---"); print(t.to_string(float_format=lambda x:f"{x:.4f}"))
# 年別の全体較正
print("\n=== 年別 平均予測 vs 実測 ===")
print(m.groupby("year").agg(n=("p3","size"),p3_pred=("p3","mean"),p3_obs=("top3_flag","mean"),
                            pw_pred=("pw","mean"),pw_obs=("win_flag","mean")).to_string(float_format=lambda x:f"{x:.4f}"))
