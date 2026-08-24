"""日次上限は ROI に影響するか（件数調整として使えるか）。"""
import pickle, numpy as np, pandas as pd
rng=np.random.default_rng(808)
Z=np.load("/tmp/design_mat.npz",allow_pickle=True)
PROB,PO,ACTI,AO,DATE,RK=(Z["PROB"].astype(float),Z["PO"].astype(float),Z["ACTI"],Z["AO"].astype(float),Z["DATE"].astype(str),Z["RK"].astype(str))
F=pd.read_pickle("/tmp/keirin_feat.pkl").set_index("race_key").reindex(RK)
O=pd.read_pickle("/tmp/overlap.pkl").set_index("race_key")
cross=O.cross.reindex(RK).fillna(True).values.astype(bool)
rt=F.race_type.astype(str).values
band=PO>=30; sc=np.where(band,PROB,-1.0); top=np.argsort(-sc,axis=1)[:,:5]
v=np.take_along_axis(band,top,1); hit=((top==ACTI[:,None])&v).any(1); npt=v.sum(1)
sump=np.where(v,np.take_along_axis(PROB,top,1),0).sum(1)
pop=np.isin(rt,["決勝","チャレンジ決勝","準決勝","チャレンジ準決勝"])&(~cross)&(npt>0)
nd=len(np.unique(DATE))
D=pd.DataFrame({"date":DATE,"hit":hit,"pay":np.where(hit,AO*100,0.0),"pts":npt,
                "sump":sump,"rk":RK})[pop].copy()
print(f"母集団: 決勝+準決勝 × 同ライン  {len(D):,}R ({len(D)/nd:.2f}件/日)")
for cap,order in [(None,None),(5,"sump"),(4,"sump"),(3,"sump"),(4,"rk"),(3,"rk")]:
    if cap is None: s=D
    else:
        idx=[]
        for day,g in D.groupby("date"):
            idx+=list(g.sort_values(order,ascending=(order=="rk")).head(cap).index)
        s=D.loc[idx]
    R=len(s); inv=s.pts.sum()*100; pay=s.pay.values; h=int(s.hit.sum())
    b=[pay[rng.integers(0,R,R)].sum()/inv*100 for _ in range(1200)]
    lbl="上限なし" if cap is None else f"上限{cap}（{'確率順' if order=='sump' else '発走順(中立)'}）"
    print(f"  {lbl:<22} {R:5d}R {R/nd:5.2f}件/日 的中 {h/R*100:5.2f}% 週{h/R*100/100*(R/nd)*7:5.2f}ヒット "
          f"ROI {pay.sum()/inv*100:6.1f}% CI[{np.percentile(b,2.5):.0f},{np.percentile(b,97.5):.0f}]")
