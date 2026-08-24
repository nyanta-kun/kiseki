"""案A を 2026-04〜08 の月次 vintage で確認。"""
import sys, numpy as np, pandas as pd
sys.path.insert(0,'.')
from src.strategy_wt import rank_7t1_stakes
D=pd.read_pickle("/tmp/months.pkl")
KES={"決勝","チャレンジ決勝"}
K=D[D.rtype.isin(KES)].copy()
K["t1_ok"]=K.t1            # 決勝系×別ライン×legs
UNIT=2000
rows=[]
for m,g in K.groupby("月"):
    nd=D[D.月==m].date.nunique()
    a=g[g.t1_ok]           # 7T1 が取る（別ライン）
    b=g[~g.t1_ok]          # 新枠が取る（同ライン等）
    ainv=sum(sum(rank_7t1_stakes(r.t1_legs).values()) for r in a.itertuples())
    apay=sum(r.t1_pay*rank_7t1_stakes(r.t1_legs)[r.t1_hit] for r in a.itertuples() if r.t1_hit)
    ah=int(a.t1_hit.notna().sum())
    binv=b.new_n.sum()*UNIT; bpay=(b.new_pay*UNIT).sum(); bh=int(b.new_hit.sum())
    rows.append(dict(月=m,
      T1_R=len(a),T1件日=round(len(a)/nd,2),T1的中=round(ah/max(len(a),1)*100,2),
      T1_ROI=round(apay/ainv*100,1) if ainv else 0,
      新枠R=len(b),新枠件日=round(len(b)/nd,2),新枠的中=round(bh/max(len(b),1)*100,2),
      新枠ROI=round(bpay/binv*100,1) if binv else 0,
      合計件日=round(len(g)/nd,2)))
T=pd.DataFrame(rows); pd.set_option("display.width",220)
print("【案A: 7T1=決勝×別ライン / 新枠=決勝×同ライン（2026-04〜08・月次vintage）】")
print(T.to_string(index=False))
a=K[K.t1_ok]; b=K[~K.t1_ok]; nd=D.date.nunique()
ainv=sum(sum(rank_7t1_stakes(r.t1_legs).values()) for r in a.itertuples())
apay=sum(r.t1_pay*rank_7t1_stakes(r.t1_legs)[r.t1_hit] for r in a.itertuples() if r.t1_hit)
binv=b.new_n.sum()*UNIT; bpay=(b.new_pay*UNIT).sum()
print(f"\n通算 4〜8月（{nd}日）")
print(f"  7T1（決勝×別ライン）: {len(a)}R {len(a)/nd:.2f}件/日 的中 {int(a.t1_hit.notna().sum())}件"
      f"({a.t1_hit.notna().sum()/len(a)*100:.2f}%) ROI {apay/ainv*100:.1f}%")
print(f"  新枠（決勝×同ライン）: {len(b)}R {len(b)/nd:.2f}件/日 的中 {int(b.new_hit.sum())}件"
      f"({b.new_hit.sum()/len(b)*100:.2f}%) ROI {bpay/binv*100:.1f}%")
print(f"  合計 {len(K)/nd:.2f}件/日")
