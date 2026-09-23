import pandas as pd, numpy as np
U=pd.read_pickle("LN/units.pkl")
R3=U[(U.lead_style=="逃")&(U.rp1_line_size<=3)&(U.lead_rk>=5)].copy()
R3["qt"]=pd.PeriodIndex(R3.date,freq="Q").astype(str)
g=R3.groupby("qt")[["st","ret"]].sum(); print("R3 四半期別ROI:",(g.ret/g.st*100).round(1).to_dict())
mx=R3.groupby("qt").apply(lambda z:(z.ret.sum()-z.ret.max())/z.st.sum()*100,include_groups=False).round(1); print("R3 最大1本除く:",mx.to_dict())
for lr in [5,6,7]:
    q=R3[R3.lead_rk==lr]; print(f"  先頭の得点順位{lr}: n={len(q)} 的中{q.hit.mean():.1%} ROI 探索{q[q.win=='探索'].ret.sum()/q[q.win=='探索'].st.sum():.1%} 確認{q[q.win=='確認'].ret.sum()/q[q.win=='確認'].st.sum():.1%}")
h=R3[R3.hit]; print("的中1本(5000円投資)の払戻 中央",int(h.ret.median())," 上位10%",int(h.ret.quantile(.9)))
rng=np.random.default_rng(0)
print("\n== 1日の本数を絞る（R3のユニットから）==")
for cap,how in [(None,"全部"),(10,"発走が早い順"),(5,"発走が早い順"),(10,"無作為"),(5,"無作為")]:
    o=[]
    for w in ["探索","確認"]:
        W=R3[R3.win==w].copy()
        if cap:
            if how=="無作為": W["o"]=rng.random(len(W))
            else: W["o"]=W.race_key.str[-2:].astype(int)   # R番号（早い順の近似）
            W=W[W.o.groupby(W.date).rank(method="first")<=cap]
        dd=W.groupby("date")[["st","ret"]].sum(); pl=dd.ret-dd.st; cum=pl.cumsum()
        m=W.assign(mo=W.date.str[:7]).groupby("mo")[["st","ret"]].sum()
        o.append(f"{w} {len(W)/len(dd):.1f}本 投資{int(dd.st.mean()):,}/日 ROI{W.ret.sum()/W.st.sum():.1%} 百超日{(dd.ret>=dd.st).mean():.1%} 百超月{(m.ret>=m.st).sum()}/{len(m)} 最大DD{int((cum-cum.cummax()).min()):,}")
    print(f"{how}{'' if not cap else cap}本:"," | ".join(o))
