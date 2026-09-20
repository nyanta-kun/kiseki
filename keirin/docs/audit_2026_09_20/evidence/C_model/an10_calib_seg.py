import sys, numpy as np, pandas as pd
sys.path.insert(0,"/Users/ysuzuki/GitHub/kiseki/keirin")
OUT="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/C_model"
CACHE="/Users/ysuzuki/GitHub/kiseki/keirin/data/feature_cache/wtfeat_20221201_20260831_f70_728566_20260831.pkl"
m=pd.read_pickle(f"{OUT}/wf_preds_audit.pkl")
meta=pd.read_pickle(CACHE)[["race_key","frame_no","grade","race_type"]]
m=m.merge(meta,on=["race_key","frame_no"],how="left")
m["n_car"]=m.groupby("race_key")["frame_no"].transform("size")
m7=m[m.n_car==7]
print("=== 種別ごとの p3 較正（予測平均 − 実測3着内率, pt）: honest WF 7車 ===")
t=m7.groupby("race_type").agg(n=("p3","size"),pred=("p3","mean"),obs=("top3_flag","mean"))
t=t[t.n>2000]; t["bias_pt"]=(t.pred-t.obs)*100
t["se_pt"]=np.sqrt(t.obs*(1-t.obs)/t.n)*100
print(t.sort_values("bias_pt",ascending=False).to_string(float_format=lambda x:f"{x:.3f}"))
print("\n=== グレード別 ===")
g=m7.groupby("grade").agg(n=("p3","size"),pred=("p3","mean"),obs=("top3_flag","mean"))
g["bias_pt"]=(g.pred-g.obs)*100; g["se_pt"]=np.sqrt(g.obs*(1-g.obs)/g.n)*100
print(g.to_string(float_format=lambda x:f"{x:.3f}"))
print("\n=== keirin.p3_calibration の主張との比較 ===")
try:
    from src.p3_calibration import __doc__ as doc
    print((doc or "")[:1200])
except Exception as e: print("import失敗", e)
