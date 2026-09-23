import pandas as pd, numpy as np
U=pd.read_pickle("LN/units4.pkl"); rng=np.random.default_rng(0)
print("相関: line_sum×mean_diff",round(U.line_sum.corr(U.mean_diff),2)," line_sum×lead_rk",round(U.line_sum.corr(U.lead_rk),2)," line_sum×line_size",round(U.line_sum.corr(U.line_size),2))
rules={
 "② 先頭が逃∧得点1位ラインが3車以下":U.index==U.index,
 "③ ②∧先頭の得点5位以下":U.lead_rk>=5,
 "A ②∧ラインの得点合計≤175":U.line_sum<=175,
 "B ②∧2車ライン∧平均得点差>4":(U.line_size==2)&(U.mean_diff>4),
 "C ③∧ラインの得点合計≤175":(U.lead_rk>=5)&(U.line_sum<=175),
 "D ③∧2車ライン":(U.lead_rk>=5)&(U.line_size==2),
}
for nm,m in rules.items():
    o=[]
    for w in ["探索","確認"]:
        q=U[m&(U.win==w)]; dd=q.groupby("date")[["st","ret"]].sum(); v=dd.values
        bs=[v[i].sum(0) for i in [rng.integers(0,len(v),len(v)) for _ in range(1000)]]; lo,hi=np.percentile([b[1]/b[0] for b in bs],[2.5,97.5])
        roi=q.ret.sum()/q.st.sum(); mx=(q.ret.sum()-q.ret.nlargest(3).sum())/q.st.sum()
        o.append(f"{w} {len(q)/dd.shape[0]:.1f}本/日 的中{q.hit.mean():.1%} ROI{roi:.1%}[{lo:.0%},{hi:.0%}] 上位3本除く{mx:.0%} 百超日{(dd.ret>=dd.st).mean():.0%}")
    print(f"{nm:32s}"," | ".join(o))
q=U[(U.lead_rk>=5)&(U.line_sum<=175)].copy(); q["qt"]=pd.PeriodIndex(q.date,freq="Q").astype(str)
g=q.groupby("qt")[["st","ret"]].sum(); print("\nC 四半期別ROI:",(g.ret/g.st*100).round(0).to_dict())
q=U[U.line_sum<=175].copy(); q["qt"]=pd.PeriodIndex(q.date,freq="Q").astype(str)
g=q.groupby("qt")[["st","ret"]].sum(); print("A 四半期別ROI:",(g.ret/g.st*100).round(0).to_dict())
h=U[(U.line_sum<=175)&U.hit]; print("A 的中1本(5000円)の払戻 中央",int(h.ret.median()),"上位10%",int(h.ret.quantile(.9)))
