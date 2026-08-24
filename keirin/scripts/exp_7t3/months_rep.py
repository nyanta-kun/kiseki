import sys, numpy as np, pandas as pd
sys.path.insert(0,'.')
from src.strategy_wt import rank_7t1_stakes
D=pd.read_pickle("/tmp/months.pkl")
# 7T1 の日次上限5（ev降順）
D["t1_final"]=False
for (m,day),g in D[D.t1].groupby(["月","date"]):
    D.loc[g.sort_values("t1_ev",ascending=False).head(5).index,"t1_final"]=True
UNIT=2000
rows=[]
for m,g in D.groupby("月"):
    nd=g.date.nunique()
    k=g[g.new]
    inv=len(k)*5*UNIT; pay=(k.new_pay*UNIT).sum(); h=int(k.new_hit.sum())
    hp=(k[k.new_hit==1].new_pay*UNIT)
    t=g[g.t1_final]
    tinv=sum(sum(rank_7t1_stakes(r.t1_legs).values()) for r in t.itertuples())
    tpay=0.0; th=0; tp=[]
    for r in t.itertuples():
        if r.t1_hit:
            s=rank_7t1_stakes(r.t1_legs)[r.t1_hit]; v=r.t1_pay*s; tpay+=v; th+=1; tp.append(v)
    rows.append(dict(月=m,日数=nd,
      新案R=len(k), 新案件日=round(len(k)/nd,1), 新案的中=round(h/max(len(k),1)*100,2),
      新案週ヒット=round(h/nd*7,2), 新案ROI=round(pay/inv*100,1),
      新案中央=int(hp.median()) if h else 0, 新案最大=int(hp.max()) if h else 0,
      T1_R=len(t), T1件日=round(len(t)/nd,1), T1的中=round(th/max(len(t),1)*100,2),
      T1_ROI=round(tpay/tinv*100,1) if tinv else 0, T1最大=int(max(tp)) if tp else 0))
T=pd.DataFrame(rows)
pd.set_option("display.width",250)
print("【2026年 月次・1レース1万円ベース】")
print(T[["月","日数","新案R","新案件日","新案的中","新案週ヒット","新案ROI","新案中央","新案最大"]].to_string(index=False))
print()
print(T[["月","T1_R","T1件日","T1的中","T1_ROI","T1最大"]].to_string(index=False))
# 4-8月 通算
k=D[D.new]; nd=D.date.nunique()
inv=len(k)*5*UNIT; pay=(k.new_pay*UNIT).sum(); h=int(k.new_hit.sum())
rng=np.random.default_rng(77); v=(k.new_pay*UNIT).values
bs=[v[rng.integers(0,len(v),len(v))].sum()/inv*100 for _ in range(4000)]
print(f"\n■ 4〜8月 通算（新案）: {len(k)}R / {nd}日 / {len(k)/nd:.1f}件日 / 的中 {h}件 {h/len(k)*100:.2f}% "
      f"/ 週 {h/nd*7:.2f}ヒット / ROI {pay/inv*100:.1f}% CI[{np.percentile(bs,2.5):.0f},{np.percentile(bs,97.5):.0f}]")
print(f"   払戻中央 {int(k[k.new_hit==1].new_pay.median()*UNIT):,}円 / 最大 {int(k.new_pay.max()*UNIT):,}円")
t=D[D.t1_final]
tinv=sum(sum(rank_7t1_stakes(r.t1_legs).values()) for r in t.itertuples())
tpay=sum(r.t1_pay*rank_7t1_stakes(r.t1_legs)[r.t1_hit] for r in t.itertuples() if r.t1_hit)
th=int(t.t1_hit.notna().sum())
print(f"■ 4〜8月 通算（7T1）: {len(t)}R / {len(t)/nd:.1f}件日 / 的中 {th}件 {th/len(t)*100:.2f}% / ROI {tpay/tinv*100:.1f}%")
both=D[D.t1_final&D.new]
print(f"■ 重なり: {len(both)}R（新案の{len(both)/max(len(k),1)*100:.1f}% / 7T1の{len(both)/max(len(t),1)*100:.1f}%）")
