import numpy as np, pandas as pd
D=pd.read_pickle("/tmp/months.pkl")
UNIT=2000
# 全レース（決勝以外も含む）× 30倍+ × 5点 を対照にする
D["allnew_hit"]=D.new_hit; D["allnew_pay"]=D.new_pay
# new_* は「決勝のみ」フラグに関係なく計算済み（new フラグだけが母集団条件）
rows=[]
for m,g in D.groupby("月"):
    nd=g.date.nunique()
    for lbl,sub in [("決勝のみ",g[g.new]),("全レース(対照)",g),
                    ("決勝以外(対照)",g[~g.rtype.isin(["決勝","チャレンジ決勝"])])]:
        s=sub[sub.new_n>0]
        if len(s)==0: continue
        inv=s.new_n.sum()*UNIT; pay=(s.new_pay*UNIT).sum(); h=int(s.new_hit.sum())
        rows.append(dict(月=m,母集団=lbl,R=len(s),件日=round(len(s)/nd,1),
          的中=round(h/len(s)*100,2),ROI=round(pay/inv*100,1)))
T=pd.DataFrame(rows)
print(T.pivot_table(index="月",columns="母集団",values=["件日","的中","ROI"],aggfunc="first")
      .reorder_levels([1,0],axis=1).sort_index(axis=1).to_string())
print("\n■ 4〜8月 通算")
rng=np.random.default_rng(101)
for lbl,sub in [("決勝のみ",D[D.new]),("全レース(対照)",D[D.new_n>0]),
                ("決勝以外(対照)",D[(~D.rtype.isin(["決勝","チャレンジ決勝"]))&(D.new_n>0)])]:
    inv=sub.new_n.sum()*UNIT; v=(sub.new_pay*UNIT).values; h=int(sub.new_hit.sum())
    bs=[v[rng.integers(0,len(v),len(v))].sum()/inv*100 for _ in range(3000)]
    print(f"  {lbl:<14} {len(sub):5d}R 的中 {h/len(sub)*100:5.2f}%  ROI {v.sum()/inv*100:6.1f}%  CI[{np.percentile(bs,2.5):.0f},{np.percentile(bs,97.5):.0f}]")
print("\n■ 参考: 2024-07〜2025-12 バックテスト（同じ買い方）")
print("  決勝のみ 90.7% / 全レース 78.6%  → 決勝プレミアム +12.1pt")
