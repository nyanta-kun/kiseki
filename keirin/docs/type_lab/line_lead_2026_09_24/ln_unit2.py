import pandas as pd, numpy as np
U=pd.read_pickle("LN/units.pkl"); F=pd.read_pickle("LN/final.pkl")[["race_key","k"]]; U=U.merge(F,on="race_key",how="left")
rng=np.random.default_rng(0)
rules={"R0 全ユニット(=L2)":U.index==U.index,
 "R1 先頭が逃":U.lead_style=="逃",
 "R2 先頭が逃 ∧ 得点1位のラインが3車以下":(U.lead_style=="逃")&(U.rp1_line_size<=3),
 "R3 R2 ∧ 先頭の得点順位5位以下":(U.lead_style=="逃")&(U.rp1_line_size<=3)&(U.lead_rk>=5),
 "R4 R2 ∧ 先頭の得点順位2-4位":(U.lead_style=="逃")&(U.rp1_line_size<=3)&(U.lead_rk<=4)}
print("== ユニット単位（1ライン=5点）全レース ==")
for nm,m in rules.items():
    o=[]
    for w in ["探索","確認"]:
        q=U[m&(U.win==w)]; dd=q.groupby("date")[["st","ret"]].sum().values
        bs=[dd[i].sum(0) for i in [rng.integers(0,len(dd),len(dd)) for _ in range(1000)]]; lo,hi=np.percentile([b[1]/b[0] for b in bs],[2.5,97.5])
        o.append(f"{w} {len(q)/q.date.nunique():.1f}本/日 的中{q.hit.mean():.1%} ROI{q.ret.sum()/q.st.sum():.1%}[{lo:.0%},{hi:.0%}]")
    print(f"{nm:34s}"," | ".join(o))
print("\n== 波乱の選別(日内上位K) × ルール（1日あたり）==")
for nm in ["R0 全ユニット(=L2)","R2 先頭が逃 ∧ 得点1位のラインが3車以下"]:
    m=rules[nm]
    for K in [5,8,12]:
        o=[]
        for w in ["探索","確認"]:
            q=U[m&(U.win==w)&(U.k<=K)]; dd=q.groupby("date")[["st","ret"]].sum()
            # 対照: 同じルールのユニットから同数を無作為
            W=U[m&(U.win==w)]; cp=[];cr=[]
            for s in range(20):
                z=W.assign(u=np.random.default_rng(s).random(len(W))); n_per=q.groupby("date").size()
                z=z[z.date.isin(n_per.index)]; z["rk"]=z.u.groupby(z.date).rank(); z=z[z.rk<=z.date.map(n_per)]
                zz=z.groupby("date")[["st","ret"]].sum(); cr.append(zz.ret.sum()/zz.st.sum()); cp.append((zz.ret>=zz.st).mean())
            o.append(f"{w} {len(q)/dd.shape[0]:.1f}本 {dd.st.mean()/1000:.0f}点 的中{q.hit.mean():.1%} ROI{q.ret.sum()/q.st.sum():.1%}(無作為{np.median(cr):.0%}) 百超日{(dd.ret>=dd.st).mean():.1%}(無作為{np.median(cp):.1%})")
        print(f"{nm[:3]} K={K:2d} "," | ".join(o))
