"""5点買いの「的中率 × 払戻」フロンティア。オッズ帯の下限を動かす。"""
import numpy as np, pandas as pd
Z=np.load("/tmp/design_mat.npz", allow_pickle=True)
PROB,PO,ACTI,AO,DATE=(Z["PROB"].astype(float),Z["PO"].astype(float),Z["ACTI"],Z["AO"],Z["DATE"].astype(str))
EXP=DATE<"2026-01-01"
rows=[]
for lo,hi in [(1,1e9),(5,1e9),(10,1e9),(20,1e9),(30,1e9),(50,1e9),(80,1e9),(100,300),(150,600)]:
    band=(PO>=lo)&(PO<hi)
    sc=np.where(band,PROB*PO if lo>=80 else PROB,-1.0)   # 低オッズ帯は確率順が自然
    top=np.argsort(-sc,axis=1)[:,:5]; v=np.take_along_axis(band,top,1)
    hit=((top==ACTI[:,None])&v).any(1); npt=v.sum(1)
    sump=np.where(v,np.take_along_axis(PROB,top,1),0).sum(1)   # モデル自身の的中確率
    for per,m in [("探索",EXP&(npt>0)),("確認",~EXP&(npt>0))]:
        pay=np.where(hit[m],AO[m]*100,0.0)
        h=hit[m]
        rows.append(dict(帯=f"{lo:g}倍以上" if hi>1e8 else f"{lo:g}-{hi:g}倍",期=per,
            R=int(m.sum()), 的中=round(h.mean()*100,2),
            週ヒット=round(h.mean()*3.5*7,2),
            払戻中央=int(np.median(pay[h])) if h.any() else 0,
            万車券率=round((pay>=10000).mean()*100,2),
            ROI=round(pay.sum()/(npt[m].sum()*100)*100,1),
            モデル予想的中=round(sump[m].mean()*100,2)))
T=pd.DataFrame(rows)
p=T.pivot_table(index="帯",columns="期",values=["的中","週ヒット","払戻中央","万車券率","ROI","モデル予想的中"],aggfunc="first")
p=p.reorder_levels([1,0],axis=1).sort_index(axis=1)
pd.set_option("display.width",250)
print("【5点買い・オッズ帯の下限を動かす】週ヒット = 1日3.5レース×7日 で期待される的中回数")
print(p.to_string())
