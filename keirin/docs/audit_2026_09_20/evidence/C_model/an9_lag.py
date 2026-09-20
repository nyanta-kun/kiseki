"""本番 type_lab が使う `lgbm_wt_eval`（学習終端が常に約90日前）の代償を測る。"""
import sys, numpy as np, pandas as pd, lightgbm as lgb, datetime as dt
from sklearn.metrics import roc_auc_score
sys.path.insert(0,"/Users/ysuzuki/GitHub/kiseki/keirin")
from src.preprocessing.feature_wt import FEATURE_COLS_WT
CACHE="/Users/ysuzuki/GitHub/kiseki/keirin/data/feature_cache/wtfeat_20221201_20260831_f70_728566_20260831.pkl"
P=dict(objective="binary",metric="auc",n_estimators=500,learning_rate=0.05,num_leaves=31,
       min_child_samples=20,colsample_bytree=0.8,random_state=42,verbose=-1,n_jobs=-1)
df=pd.read_pickle(CACHE); df=df[df.finish_order.notna()].copy()
df["race_date"]=df["race_date"].astype(str)
X=df.reindex(columns=FEATURE_COLS_WT).fillna(0).values.astype(np.float32)
Xd=pd.DataFrame(X,columns=FEATURE_COLS_WT)
rows=[]
for ms in ["2026-01","2026-02","2026-03","2026-04","2026-05","2026-06","2026-07","2026-08"]:
    lo=f"{ms}-01"
    hi=(dt.date.fromisoformat(lo)+dt.timedelta(days=32)).replace(day=1)-dt.timedelta(days=1)
    hi=hi.isoformat()
    te=((df.race_date>=lo)&(df.race_date<=hi)).values
    lag=(dt.date.fromisoformat(lo)-dt.timedelta(days=90)).isoformat()
    for nm,cut in (("expanding(lgbm_wt相当)",lo),("lag90(lgbm_wt_eval相当)",lag)):
        tr=(df.race_date<cut).values
        m=lgb.LGBMClassifier(**P).fit(Xd[tr],df.loc[tr,"top3_flag"].values)
        p=m.predict_proba(Xd[te])[:,1]
        d=df.loc[te,["race_key","frame_no","top3_flag","win_flag"]].copy(); d["p"]=p
        d["r"]=d.groupby("race_key")["p"].rank(ascending=False,method="first")
        t2=d[d.r<=2].groupby("race_key")["top3_flag"].sum()
        rows.append(dict(month=ms,arm=nm,n=int(te.sum()),auc=roc_auc_score(d.top3_flag,d.p),
                         top1_win=d[d.r==1].win_flag.mean(),top1_t3=d[d.r==1].top3_flag.mean(),
                         axis2=(t2==2).mean()))
        print(rows[-1],flush=True)
r=pd.DataFrame(rows)
print(r.pivot_table(index="month",columns="arm",values=["auc","top1_win","axis2"]).to_string(float_format=lambda x:f"{x:.4f}"))
print("\n平均差 (expanding - lag90):")
piv=r.pivot_table(index="month",columns="arm",values=["auc","top1_win","top1_t3","axis2"])
for k in ["auc","top1_win","top1_t3","axis2"]:
    print(f"  {k}: {(piv[k]['expanding(lgbm_wt相当)']-piv[k]['lag90(lgbm_wt_eval相当)']).mean():+.5f}")
