import numpy as np, pandas as pd
OUT="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/C_model"
m=pd.read_pickle(f"{OUT}/wf_preds_audit.pkl")
m["n_car"]=m.groupby("race_key")["frame_no"].transform("size")
m["r"]=m.groupby("race_key")["p3"].rank(ascending=False,method="first")
top2=m[m.r<=2].groupby("race_key").agg(axis_sum=("p3","sum"),both=("top3_flag","sum"),
                                       n_car=("n_car","first"),date=("race_date","first"))
top2["both"]=(top2.both==2).astype(int)
print("=== axis_sum(honest WF・生p3上位2合計) 1.44 境界の分離（本番 AXIS_SUM_FIRM）===")
for n in (7,9):
    d=top2[top2.n_car==n]
    firm=d[d.axis_sum>=1.44]; soft=d[d.axis_sum<1.44]
    print(f"{n}車 n={len(d):,}  堅い割合 {len(firm)/len(d):.4f}  "
          f"二軸そろい 堅い {firm.both.mean():.4f} / 混戦 {soft.both.mean():.4f}  差 {(firm.both.mean()-soft.both.mean())*100:+.2f}pt")
print("\n=== axis_sum 十分位 × 二軸そろい（7車）===")
d=top2[top2.n_car==7].copy(); d["q"]=pd.qcut(d.axis_sum,10,labels=False)
print(d.groupby("q").agg(n=("both","size"),axis_sum=("axis_sum","mean"),both=("both","mean"))
      .to_string(float_format=lambda x:f"{x:.4f}"))
print("\n=== 年別の堅い割合（水準ドリフト）===")
d2=top2[top2.n_car==7].copy(); d2["y"]=d2.date.str[:4]
print(d2.groupby("y").agg(n=("both","size"),mean_axis=("axis_sum","mean"),
                          firm=("axis_sum",lambda s:(s>=1.44).mean()),both=("both","mean"))
      .to_string(float_format=lambda x:f"{x:.4f}"))
