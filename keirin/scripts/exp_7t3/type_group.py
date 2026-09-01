"""種別グループの妥当性を四半期7窓で検証（多重比較への防御）。"""
import numpy as np, pandas as pd
rng=np.random.default_rng(1515)
Z=np.load("/tmp/design_mat.npz",allow_pickle=True)
PROB,PO,ACTI,AO,DATE,RK=(Z["PROB"].astype(float),Z["PO"].astype(float),Z["ACTI"],Z["AO"].astype(float),Z["DATE"].astype(str),Z["RK"].astype(str))
F=pd.read_pickle("/tmp/keirin_feat.pkl").set_index("race_key").reindex(RK)
rt=F.race_type.astype(str).values
band=PO>=30; sc=np.where(band,PROB,-1.0); top=np.argsort(-sc,axis=1)[:,:5]
v=np.take_along_axis(band,top,1); hit=((top==ACTI[:,None])&v).any(1); npt=v.sum(1)
GOOD=["決勝","チャレンジ決勝","準決勝","チャレンジ準決勝","予選","ガールズ予選(第１走)"]
BAD=["選抜","チャレンジ選抜","チャレンジ一般","初特選","チャレンジ予選","一般","特一般","特選"]
G=np.isin(rt,GOOD)&(npt>0); B=np.isin(rt,BAD)&(npt>0); ALL=(npt>0)
Q=[("2024H2","2024-07-01","2024-12-31"),("2025Q1","2025-01-01","2025-03-31"),
   ("2025Q2","2025-04-01","2025-06-30"),("2025Q3","2025-07-01","2025-09-30"),
   ("2025Q4","2025-10-01","2025-12-31"),("2026Q1","2026-01-01","2026-03-31"),
   ("2026Q2+","2026-04-01","2026-08-04")]
rows=[]
for nm,a,b in Q:
    w=(DATE>=a)&(DATE<=b); nd=len(np.unique(DATE[w]))
    for lbl,m0 in [("採用群",G),("除外群",B),("全レース",ALL)]:
        m=w&m0; R=int(m.sum()); pay=np.where(hit[m],AO[m]*100,0.0)
        rows.append(dict(窓=nm,群=lbl,件日=round(R/nd,1),的中=round(hit[m].mean()*100,2),
                         ROI=round(pay.sum()/(npt[m].sum()*100)*100,1)))
T=pd.DataFrame(rows)
print("【種別グループ × 四半期7窓】壁=74.85%")
print(T.pivot_table(index="窓",columns="群",values=["件日","的中","ROI"],aggfunc="first")
      .reorder_levels([1,0],axis=1).sort_index(axis=1).to_string())
g=T[T.群=="採用群"]; b=T[T.群=="除外群"]; al=T[T.群=="全レース"]
print(f"\n採用群が壁超え: {int((g.ROI>74.85).sum())}/7 窓 / 全レース超え: {int((g.ROI.values>al.ROI.values).sum())}/7")
print(f"除外群が壁超え: {int((b.ROI>74.85).sum())}/7 窓")
for lbl,m0 in [("採用群",G),("除外群",B),("全レース",ALL)]:
    R=int(m0.sum()); pay=np.where(hit[m0],AO[m0]*100,0.0); inv=npt[m0].sum()*100
    r=np.zeros(R); r[hit[m0]]=pay[hit[m0]]
    bs=[r[rng.integers(0,R,R)].sum()/inv*100 for _ in range(2500)]
    nd=len(np.unique(DATE))
    print(f"通算 {lbl}: {R:6,}R {R/nd:5.2f}件/日 的中 {hit[m0].mean()*100:5.2f}% "
          f"ROI {pay.sum()/inv*100:5.1f}% CI[{np.percentile(bs,2.5):.0f},{np.percentile(bs,97.5):.0f}] "
          f"払戻中央 {int(np.median(pay[hit[m0]])*20):,}円")
