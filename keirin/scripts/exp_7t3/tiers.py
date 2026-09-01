"""件数を選べるように段階（tier）で提示。両窓＋7窓の壁超え回数つき。"""
import numpy as np, pandas as pd
rng=np.random.default_rng(1616)
Z=np.load("/tmp/design_mat.npz",allow_pickle=True)
PROB,PO,ACTI,AO,DATE,RK=(Z["PROB"].astype(float),Z["PO"].astype(float),Z["ACTI"],Z["AO"].astype(float),Z["DATE"].astype(str),Z["RK"].astype(str))
F=pd.read_pickle("/tmp/keirin_feat.pkl").set_index("race_key").reindex(RK)
rt=F.race_type.astype(str).values
band=PO>=30; sc=np.where(band,PROB,-1.0); top=np.argsort(-sc,axis=1)[:,:5]
v=np.take_along_axis(band,top,1); hit=((top==ACTI[:,None])&v).any(1); npt=v.sum(1)
nd=len(np.unique(DATE))
Q=[("2024-07-01","2024-12-31"),("2025-01-01","2025-03-31"),("2025-04-01","2025-06-30"),
   ("2025-07-01","2025-09-30"),("2025-10-01","2025-12-31"),("2026-01-01","2026-03-31"),
   ("2026-04-01","2026-08-04")]
T1=["決勝","チャレンジ決勝"]
T2=T1+["準決勝","チャレンジ準決勝"]
T3=T2+["予選","ガールズ予選(第１走)"]
T4=T3+["特予選","ガールズ決勝","ガールズ予選(第２走)"]
rows=[]
for lbl,types in [("Tier1 決勝系",T1),("Tier2 +準決勝系",T2),("Tier3 +予選系",T3),
                  ("Tier4 +特予選・ガールズ",T4),("全レース",list(set(rt)))]:
    m=np.isin(rt,types)&(npt>0); R=int(m.sum())
    pay=np.where(hit[m],AO[m]*100,0.0); inv=npt[m].sum()*100
    r=np.zeros(R); r[hit[m]]=pay[hit[m]]
    bs=[r[rng.integers(0,R,R)].sum()/inv*100 for _ in range(2000)]
    w=0
    for a,b in Q:
        wm=m&(DATE>=a)&(DATE<=b)
        if wm.sum()<100: continue
        p2=np.where(hit[wm],AO[wm]*100,0.0)
        w+=int(p2.sum()/(npt[wm].sum()*100)*100>74.85)
    rows.append(dict(母集団=lbl,R=R,件日=round(R/nd,2),的中=round(hit[m].mean()*100,2),
      週ヒット=round(hit[m].mean()*(R/nd)*7,2), ROI=round(pay.sum()/inv*100,1),
      CI=f"[{np.percentile(bs,2.5):.0f},{np.percentile(bs,97.5):.0f}]",
      壁超窓=f"{w}/7", 払戻中央=int(np.median(pay[hit[m]])*20)))
print("【7車・段階別（予測30倍以上×確率上位5点・1レース1万円換算）壁=74.85%】")
print(pd.DataFrame(rows).to_string(index=False))
