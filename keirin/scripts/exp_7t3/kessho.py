"""決勝系に絞ったときの実力（1日3〜5レース狙い）。"""
import numpy as np, pandas as pd
rng=np.random.default_rng(21)
Z=np.load("/tmp/design_mat.npz", allow_pickle=True)
PROB,PO,ACTI,AO,DATE,RK=(Z["PROB"].astype(float),Z["PO"].astype(float),Z["ACTI"],
  Z["AO"],Z["DATE"].astype(str),Z["RK"].astype(str))
F=pd.read_pickle("/tmp/keirin_feat.pkl").set_index("race_key").reindex(RK)
rt=F.race_type.astype(str).values
EXP=DATE<"2026-01-01"; days={"探索":len(np.unique(DATE[EXP])),"確認":len(np.unique(DATE[~EXP]))}
print("種別の内訳:", pd.Series(rt).value_counts().head(20).to_dict())
POPS={
 "決勝のみ": np.isin(rt,["決勝","チャレンジ決勝"]),
 "決勝+ガールズ決勝": np.isin(rt,["決勝","チャレンジ決勝","ガールズ決勝"]),
 "決勝系(決勝+準決勝)": np.isin(rt,["決勝","チャレンジ決勝","準決勝","チャレンジ準決勝"]),
 "全レース": np.ones(len(rt),bool),
}
rows=[]
for pn,pm in POPS.items():
    for lo in [30,50]:
        for n in [5,8]:
            band=PO>=lo; sc=np.where(band,PROB,-1.0); top=np.argsort(-sc,axis=1)[:,:n]
            v=np.take_along_axis(band,top,1); hit=((top==ACTI[:,None])&v).any(1); npt=v.sum(1)
            for per,m0 in [("探索",EXP),("確認",~EXP)]:
                m=m0&pm&(npt>0); R=int(m.sum())
                if R<150: continue
                pay=np.where(hit[m],AO[m]*100,0.0); inv=npt[m].sum()*100
                ret=np.zeros(R); ret[hit[m]]=pay[hit[m]]
                bs=[ret[rng.integers(0,R,R)].sum()/inv*100 for _ in range(1500)]
                rows.append(dict(母集団=pn,帯=f"{lo}倍+",点数=n,期=per,R=R,
                  件日=round(R/days[per],1), 的中=round(hit[m].mean()*100,2),
                  週ヒット=round(hit[m].mean()*(R/days[per])*7,2),
                  払戻中央=int(np.median(pay[hit[m]])), 最大=int(pay.max()),
                  ROI=round(pay.sum()/inv*100,1), CI=f"[{np.percentile(bs,2.5):.0f},{np.percentile(bs,97.5):.0f}]"))
T=pd.DataFrame(rows)
pd.set_option("display.width",260)
for pn in POPS:
    s=T[T.母集団==pn]
    if s.empty: continue
    print(f"\n=== {pn} ===")
    print(s.pivot_table(index=["帯","点数"],columns="期",
      values=["件日","的中","週ヒット","払戻中央","最大","ROI"],aggfunc="first")
      .reorder_levels([1,0],axis=1).sort_index(axis=1).to_string())
    print("  CI:", s[s.期=="確認"].set_index(["帯","点数"]).CI.to_dict())
