import sys, numpy as np, pandas as pd
sys.path.insert(0,"/Users/ysuzuki/GitHub/kiseki/keirin")
from src.models.trainer import load_model
from src.preprocessing.feature_wt import FEATURE_COLS_WT
OUT="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/C_model"
CACHE="/Users/ysuzuki/GitHub/kiseki/keirin/data/feature_cache/wtfeat_20221201_20260831_f70_728566_20260831.pkl"
df=pd.read_pickle(CACHE); df["race_date"]=df["race_date"].astype(str)
d=df[(df.race_date>="2026-08-27")&(df.race_date<="2026-08-31")].copy()
X=d.reindex(columns=FEATURE_COLS_WT).fillna(0)
for nm in ("lgbm_wt_eval","lgbm_wt"):
    d[nm]=load_model(nm).predict_proba(X)[:,1]
print("2026-08-27..31 entries:", len(d))
print("平均 p3  lgbm_wt_eval=%.4f  lgbm_wt=%.4f  corr=%.5f"
      %(d.lgbm_wt_eval.mean(), d.lgbm_wt.mean(), np.corrcoef(d.lgbm_wt_eval,d.lgbm_wt)[0,1]))
# レース内順位の一致
for nm in ("lgbm_wt_eval","lgbm_wt"):
    d[f"r_{nm}"]=d.groupby("race_key")[nm].rank(ascending=False,method="first")
r=d.groupby("race_key").apply(lambda g:(
    set(g.nsmallest(2,"r_lgbm_wt_eval").frame_no)==set(g.nsmallest(2,"r_lgbm_wt").frame_no)),
    include_groups=False)
print("2モデルで上位2車が一致するレース:", f"{r.mean():.4f}", "n=",len(r))
# axis_sum（生 p3 上位2合計）の水準差と型境界
for nm in ("lgbm_wt_eval","lgbm_wt"):
    s=d.sort_values(nm,ascending=False).groupby("race_key")[nm].apply(lambda x:x.head(2).sum())
    print(f"{nm}: axis_sum 平均 {s.mean():.4f}  1.44以上(堅い)の割合 {(s>=1.44).mean():.4f}")

# 本番が朝に保存した値との比較
p=pd.read_csv(f"{OUT}/live_picks.csv")
p=p[(p.race_date>="2026-08-27")&(p.race_date<="2026-08-31")&(p["mode"]=="live")]
mine=d[d.groupby("race_key")["frame_no"].transform("size")==7]
ord_mine=(mine.sort_values(["race_key","lgbm_wt_eval"],ascending=[True,False])
          .groupby("race_key")["frame_no"].apply(lambda s:"-".join(map(str,s))).rename("p3_order_now"))
sum_mine=(mine.sort_values(["race_key","lgbm_wt_eval"],ascending=[True,False])
          .groupby("race_key")["lgbm_wt_eval"].apply(lambda s:s.head(2).sum()).rename("axis_sum_now"))
j=p.merge(ord_mine,on="race_key").merge(sum_mine,on="race_key")
j["ax1_now"]=j.p3_order_now.str.split("-").str[0].astype(int)
j["ax2_now"]=j.p3_order_now.str.split("-").str[1].astype(int)
j["set_same"]=[set([a,b])==set([c,dd]) for a,b,c,dd in zip(j.axis1,j.axis2,j.ax1_now,j.ax2_now)]
print("\n=== 朝に保存された出力 vs いまのモデル×いまの特徴量で再計算（n=%d レース）==="%len(j))
print("p3_order 完全一致 %.4f / 上位2車の集合一致 %.4f / axis1 一致 %.4f"
      %( (j.p3_order==j.p3_order_now).mean(), j.set_same.mean(), (j.axis1==j.ax1_now).mean()))
print("axis_sum 差: mean %+.5f  絶対値中央 %.5f  |差|>0.02 の割合 %.4f"
      %((j.axis_sum_now-j.axis_sum).mean(), (j.axis_sum_now-j.axis_sum).abs().median(),
        ((j.axis_sum_now-j.axis_sum).abs()>0.02).mean()))
print("型境界 1.44 をまたぐ割合 %.4f"%(((j.axis_sum>=1.44)!=(j.axis_sum_now>=1.44)).mean()))
