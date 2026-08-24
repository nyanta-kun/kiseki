"""『この5点が当たるレースか』を予測する源泉モデル。Σp単独を超えられるか。"""
import numpy as np, pandas as pd, lightgbm as lgb
from sklearn.metrics import roc_auc_score
Z=np.load("/tmp/design_mat.npz", allow_pickle=True)
PROB,PO,ACTI,AO,DATE,RK=(Z["PROB"].astype(float),Z["PO"].astype(float),Z["ACTI"],
  Z["AO"],Z["DATE"].astype(str),Z["RK"].astype(str))
F=pd.read_pickle("/tmp/keirin_feat.pkl").set_index("race_key")
EXP=DATE<"2026-01-01"; days={"探索":len(np.unique(DATE[EXP])),"確認":len(np.unique(DATE[~EXP]))}
LO,N=30,5
band=PO>=LO
sc=np.where(band,PROB,-1.0); top=np.argsort(-sc,axis=1)[:,:N]
v=np.take_along_axis(band,top,1)
hit=((top==ACTI[:,None])&v).any(1); npt=v.sum(1)
ptop=np.take_along_axis(PROB,top,1)*v; otop=np.take_along_axis(PO,top,1)
sump=ptop.sum(1)
# 追加の盤面特徴
extra=pd.DataFrame({
 "sump":sump, "p_max":ptop.max(1), "p_min":ptop.min(1),
 "odds_med":np.median(np.where(v,otop,np.nan),axis=1),
 "odds_min":np.nanmin(np.where(v,otop,np.nan),axis=1),
 "n_band":band.sum(1), "ev_sum":(ptop*otop).sum(1),
 "p_top1_all":PROB.max(1), "p_top5_all":np.sort(PROB,1)[:,-5:].sum(1),
}, index=RK)
base=F.reindex(RK)
X=pd.concat([extra, base.drop(columns=[c for c in ["race_date","payout","tf_payout",
   "tf_win10k","tf_mkt10k","mkt_p_favset"] if c in base.columns])],axis=1)
y=hit.astype(int)
ok=npt>0
tr=EXP&ok; iv=(DATE>="2025-10-01")&tr; te=(~EXP)&ok
FEATS=[c for c in X.columns]
m=lgb.train(dict(objective="binary",metric="auc",learning_rate=0.03,num_leaves=31,
 min_data_in_leaf=300,feature_fraction=0.8,bagging_fraction=0.8,bagging_freq=1,
 lambda_l2=5.0,verbose=-1,seed=42),
 lgb.Dataset(X[tr&~iv][FEATS],y[tr&~iv]),3000,valid_sets=[lgb.Dataset(X[iv][FEATS],y[iv])],
 callbacks=[lgb.early_stopping(100,verbose=False)])
s=m.predict(X[FEATS],num_iteration=m.best_iteration)
print(f"目的: {LO}倍以上×{N}点 が的中するか  基準率(確認) {y[te].mean()*100:.2f}%  木 {m.best_iteration}")
print(f"  確認窓 AUC:  Σp単独 {roc_auc_score(y[te],sump[te]):.4f}   源泉モデル {roc_auc_score(y[te],s[te]):.4f}")
imp=pd.Series(m.feature_importance('gain'),index=FEATS).sort_values(ascending=False)
print("  gain上位8:", ", ".join(f"{k}({v/imp.sum()*100:.1f}%)" for k,v in imp.head(8).items()))
print("\n【較正】Σp が言う的中確率 vs 実際（確認窓）")
q=pd.qcut(sump[te],5,labels=False)
t=pd.DataFrame({"q":q,"Σp":sump[te]*100,"actual":y[te]*100,"odds":np.where(hit[te],AO[te],np.nan)})
print(t.groupby("q").agg(n=("Σp","size"),Σp予想=("Σp","mean"),実際=("actual","mean")).round(2).to_string())
print("\n【3〜5件/日に絞ったときの実力】")
rows=[]
for nm,score in [("Σp",sump),("源泉モデル",s)]:
    for qq,lab in [(0.94,"上位6%"),(0.96,"上位4%")]:
        thr=np.quantile(score[tr],qq); g=score>=thr
        for per,m0 in [("探索",EXP),("確認",~EXP)]:
            mm=m0&g&ok; R=int(mm.sum())
            pay=np.where(hit[mm],AO[mm]*100,0.0)
            rows.append(dict(選別=f"{nm} {lab}",期=per,件日=round(R/days[per],1),
              的中=round(hit[mm].mean()*100,2), 週ヒット=round(hit[mm].mean()*(R/days[per])*7,2),
              払戻中央=int(np.median(pay[hit[mm]])), ROI=round(pay.sum()/(npt[mm].sum()*100)*100,1)))
print(pd.DataFrame(rows).pivot_table(index="選別",columns="期",
  values=["件日","的中","週ヒット","払戻中央","ROI"],aggfunc="first")
  .reorder_levels([1,0],axis=1).sort_index(axis=1).to_string())
