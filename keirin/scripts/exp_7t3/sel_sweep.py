"""1日3〜5レースに絞る前提で、レース選別と点数がどこまで押し上げるか。"""
import numpy as np, pandas as pd
Z=np.load("/tmp/design_mat.npz", allow_pickle=True)
PROB,PO,ACTI,AO,DATE,P1ENT=(Z["PROB"].astype(float),Z["PO"].astype(float),Z["ACTI"],
  Z["AO"],Z["DATE"].astype(str),Z["P1ENT"])
EXP=DATE<"2026-01-01"
days={"探索":len(np.unique(DATE[EXP])),"確認":len(np.unique(DATE[~EXP]))}

def build(lo,n):
    band=PO>=lo
    sc=np.where(band,PROB,-1.0)
    top=np.argsort(-sc,axis=1)[:,:n]; v=np.take_along_axis(band,top,1)
    hit=((top==ACTI[:,None])&v).any(1); npt=v.sum(1)
    sump=np.where(v,np.take_along_axis(PROB,top,1),0).sum(1)
    return hit,npt,sump

rows=[]
for lo in [20,30,50]:
    for n in [5,8,12]:
        hit,npt,sump=build(lo,n)
        for selname,q in [("選別なし",None),("Σp上位20%",0.80),("Σp上位10%",0.90),("Σp上位6%",0.94),
                          ("堅いレース上位10%",None)]:
            if selname.startswith("Σp"):
                thr=np.quantile(sump[EXP&(npt>0)],q); g=sump>=thr
            elif selname.startswith("堅い"):
                thr=np.quantile(P1ENT[EXP],0.10); g=P1ENT<=thr
            else:
                g=np.ones(len(PO),bool)
            for per,m0 in [("探索",EXP),("確認",~EXP)]:
                m=m0&g&(npt>0); R=int(m.sum())
                if R<100: continue
                h=hit[m]; pay=np.where(h,AO[m]*100,0.0)
                rows.append(dict(帯=f"{lo}倍+",点数=n,選別=selname,期=per,
                  件日=round(R/days[per],1), 的中=round(h.mean()*100,2),
                  週ヒット=round(h.mean()*(R/days[per])*7,2),
                  払戻中央=int(np.median(pay[h])) if h.any() else 0,
                  ROI=round(pay.sum()/(npt[m].sum()*100)*100,1)))
T=pd.DataFrame(rows)
pd.set_option("display.width",260); pd.set_option("display.max_rows",300)
p=T.pivot_table(index=["帯","点数","選別"],columns="期",values=["件日","的中","週ヒット","払戻中央","ROI"],aggfunc="first")
p=p.reorder_levels([1,0],axis=1).sort_index(axis=1)
print(p.to_string())
