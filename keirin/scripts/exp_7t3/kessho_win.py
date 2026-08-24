import numpy as np, pandas as pd
Z=np.load("/tmp/design_mat.npz", allow_pickle=True)
PROB,PO,ACTI,AO,DATE,RK=(Z["PROB"].astype(float),Z["PO"].astype(float),Z["ACTI"],
  Z["AO"],Z["DATE"].astype(str),Z["RK"].astype(str))
F=pd.read_pickle("/tmp/keirin_feat.pkl").set_index("race_key").reindex(RK)
rt=F.race_type.astype(str).values
pop=np.isin(rt,["決勝","チャレンジ決勝"])
band=PO>=30; sc=np.where(band,PROB,-1.0); top=np.argsort(-sc,axis=1)[:,:5]
v=np.take_along_axis(band,top,1); hit=((top==ACTI[:,None])&v).any(1); npt=v.sum(1)
Q=[("2024H2","2024-07-01","2024-12-31"),("2025Q1","2025-01-01","2025-03-31"),
   ("2025Q2","2025-04-01","2025-06-30"),("2025Q3","2025-07-01","2025-09-30"),
   ("2025Q4","2025-10-01","2025-12-31"),("2026Q1","2026-01-01","2026-03-31"),
   ("2026Q2+","2026-04-01","2026-08-04")]
rows=[]
for nm,a,b in Q:
    w=(DATE>=a)&(DATE<=b); nd=len(np.unique(DATE[w]))
    for lbl,g in [("決勝のみ",pop),("全レース",np.ones(len(rt),bool))]:
        m=w&g&(npt>0); R=int(m.sum())
        pay=np.where(hit[m],AO[m]*100,0.0)
        rows.append(dict(窓=nm,母集団=lbl,件日=round(R/nd,1),R=R,的中=round(hit[m].mean()*100,2),
          週ヒット=round(hit[m].mean()*(R/nd)*7,2),ROI=round(pay.sum()/(npt[m].sum()*100)*100,1)))
T=pd.DataFrame(rows)
print("【30倍以上×5点・窓別】")
print(T.pivot_table(index="窓",columns="母集団",values=["件日","的中","週ヒット","ROI"],aggfunc="first")
      .reorder_levels([1,0],axis=1).sort_index(axis=1).to_string())
k=T[T.母集団=="決勝のみ"]; z=T[T.母集団=="全レース"]
print(f"\n決勝のみが全レースを上回った窓: {int((k.ROI.values>z.ROI.values).sum())}/7")
print(f"決勝のみが壁(74.85%)超え: {int((k.ROI>74.85).sum())}/7 / ROI 中央 {k.ROI.median():.1f}% 最小 {k.ROI.min():.1f}% 最大 {k.ROI.max():.1f}%")
