"""7車: 全種別を総当たりで走査（予測オッズ30倍+×確率上位5点）。両窓を並べる。"""
import numpy as np, pandas as pd
rng=np.random.default_rng(1414)
Z=np.load("/tmp/design_mat.npz",allow_pickle=True)
PROB,PO,ACTI,AO,DATE,RK=(Z["PROB"].astype(float),Z["PO"].astype(float),Z["ACTI"],Z["AO"].astype(float),Z["DATE"].astype(str),Z["RK"].astype(str))
F=pd.read_pickle("/tmp/keirin_feat.pkl").set_index("race_key").reindex(RK)
rt=F.race_type.astype(str).values
EXP=DATE<"2026-01-01"
band=PO>=30; sc=np.where(band,PROB,-1.0); top=np.argsort(-sc,axis=1)[:,:5]
v=np.take_along_axis(band,top,1); hit=((top==ACTI[:,None])&v).any(1); npt=v.sum(1)
ndE=len(np.unique(DATE[EXP])); ndC=len(np.unique(DATE[~EXP]))
rows=[]
for t in pd.Series(rt).value_counts().index:
    m0=(rt==t)&(npt>0)
    a=m0&EXP; b=m0&~EXP
    if a.sum()<150 or b.sum()<60: continue
    def st(m,nd):
        R=int(m.sum()); pay=np.where(hit[m],AO[m]*100,0.0); inv=npt[m].sum()*100
        return dict(R=R,件日=round(R/nd,2),的中=round(hit[m].mean()*100,2),
                    ROI=round(pay.sum()/inv*100,1),
                    中央=int(np.median(pay[hit[m]])*20) if hit[m].any() else 0)
    A,B=st(a,ndE),st(b,ndC)
    rows.append(dict(種別=t,探索R=A["R"],探索的中=A["的中"],探索ROI=A["ROI"],
                     確認R=B["R"],確認件日=B["件日"],確認的中=B["的中"],確認ROI=B["ROI"],
                     払戻中央=B["中央"], 両窓壁超=int(A["ROI"]>74.85)+int(B["ROI"]>74.85)))
T=pd.DataFrame(rows).sort_values("確認ROI",ascending=False)
pd.set_option("display.width",250)
print("【7車・種別総当たり（予測30倍以上×確率上位5点）壁=74.85%】")
print(T.to_string(index=False))
print("\n■ 両窓とも壁超えの種別:", T[T.両窓壁超==2].種別.tolist())
g=T[T.両窓壁超==2]
if len(g):
    import numpy as _n
    print(f"  合計 {g.確認件日.sum():.2f}件/日")
