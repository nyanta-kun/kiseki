import numpy as np, pandas as pd
OUT="/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/C_model"
p=pd.read_csv(f"{OUT}/live_picks.csv")
r=pd.read_csv(f"{OUT}/live_results.csv")
r=r[r.finish_order.notna()]
top3=r[r.finish_order.between(1,3)].groupby("race_key")["frame_no"].apply(set).rename("t3")
win1=r[r.finish_order==1].groupby("race_key")["frame_no"].first().rename("w1")
nfin=r.groupby("race_key")["finish_order"].apply(lambda s:(s>=1).sum()).rename("nfin")
d=p.merge(top3,on="race_key").merge(win1,on="race_key").merge(nfin,on="race_key")
d=d[d.nfin>=3]
d["a1_win"]=(d.axis1==d.w1).astype(int)
d["a1_t3"]=[int(a in t) for a,t in zip(d.axis1,d.t3)]
d["a2_t3"]=[int(a in t) for a,t in zip(d.axis2,d.t3)]
d["both"]=d.a1_t3*d.a2_t3
# p3_order の1位
d["idx1"]=d.p3_order.str.split("-").str[0].astype(int)
d["i1_win"]=(d.idx1==d.w1).astype(int)
d["i1_t3"]=[int(a in t) for a,t in zip(d.idx1,d.t3)]
print("=== 本番の前向き実績（type_lab_picks mode=live/live9・朝に保存された軸）===")
for nm,g in [("ALL",d)]+[(f"mode {m}",x) for m,x in d.groupby("mode")]:
    n=len(g)
    print(f"{nm:10s} n={n:5d}  axis1 1着 {g.a1_win.mean():.4f}  axis1 3着内 {g.a1_t3.mean():.4f}  "
          f"二軸そろい {g.both.mean():.4f}  p3_order1位 1着 {g.i1_win.mean():.4f} 3着内 {g.i1_t3.mean():.4f}")
print("\n週別（live=7車のみ）")
g7=d[d["mode"]=="live"].copy(); g7["wk"]=pd.to_datetime(g7.race_date).dt.to_period("W").astype(str)
print(g7.groupby("wk").agg(n=("both","size"),a1_win=("a1_win","mean"),a1_t3=("a1_t3","mean"),
      both=("both","mean")).to_string(float_format=lambda x:f"{x:.4f}"))
def ci(x):
    n=len(x); m=x.mean(); s=1.96*np.sqrt(m*(1-m)/n); return m-s,m+s
g=d[d["mode"]=="live"]
print("\n7車 live: n=%d axis1 1着=%.4f CI[%.4f,%.4f] / 二軸そろい=%.4f CI[%.4f,%.4f]"
      %(len(g),g.a1_win.mean(),*ci(g.a1_win),g.both.mean(),*ci(g.both)))
d.to_pickle(f"{OUT}/forward.pkl")
