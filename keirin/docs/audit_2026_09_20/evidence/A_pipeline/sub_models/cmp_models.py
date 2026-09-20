import sys, numpy as np, pandas as pd
sys.path.insert(0,'.')
from src.models.trainer import load_model
from src.preprocessing.feature_wt import build_features_wt, load_raw_data_wt, prepare_X
day = sys.argv[1]
feats = build_features_wt(load_raw_data_wt(min_date=day, max_date=day))
X = prepare_X(feats)
print("rows", len(feats), "races", feats['race_key'].nunique())
res = {}
for name in ["lgbm_wt_eval","lgbm_wt","lgbm_wt_win","lgbm_wt_win_eval"]:
    res[name] = load_model(name).predict_proba(X)[:,1]
d = pd.DataFrame({"rk":feats["race_key"].values,"fn":feats["frame_no"].values, **res})
# 7-car races only
cnt = d.groupby("rk")["fn"].count()
keys7 = cnt[cnt==7].index
d7 = d[d.rk.isin(keys7)]
print("7car races:", len(keys7))
print("p3 mean eval %.4f  wt %.4f  corr %.4f  MAE %.4f" % (
    d7.lgbm_wt_eval.mean(), d7.lgbm_wt.mean(),
    np.corrcoef(d7.lgbm_wt_eval, d7.lgbm_wt)[0,1],
    np.abs(d7.lgbm_wt_eval-d7.lgbm_wt).mean()))
# top2 sum vs 1.44 boundary
rows=[]
for rk,g in d7.groupby("rk"):
    a=sorted(g.lgbm_wt_eval, reverse=True); b=sorted(g.lgbm_wt, reverse=True)
    rows.append((rk, a[0]+a[1], b[0]+b[1],
                 tuple(g.sort_values("lgbm_wt_eval",ascending=False).fn[:2]),
                 tuple(g.sort_values("lgbm_wt",ascending=False).fn[:2])))
t=pd.DataFrame(rows,columns=["rk","s_eval","s_wt","top2_eval","top2_wt"])
print("axis_sum mean eval %.4f wt %.4f" % (t.s_eval.mean(), t.s_wt.mean()))
for thr in (1.44,):
    print("firm(>=%.2f) eval %d/%d  wt %d/%d  disagree %d" % (
        thr,(t.s_eval>=thr).sum(),len(t),(t.s_wt>=thr).sum(),len(t),
        ((t.s_eval>=thr)!=(t.s_wt>=thr)).sum()))
print("top2 set differs:", (t.apply(lambda r: set(r.top2_eval)!=set(r.top2_wt),axis=1)).sum(), "/", len(t))
