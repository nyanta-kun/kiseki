"""場・種別・級班が本物の源泉か（探索窓の順位が確認窓で再現するか）。"""
import numpy as np, pandas as pd
from scipy.stats import spearmanr
Z=np.load("/tmp/design_mat.npz", allow_pickle=True)
PROB,PO,ACTI,AO,DATE,RK=(Z["PROB"].astype(float),Z["PO"].astype(float),Z["ACTI"],
  Z["AO"],Z["DATE"].astype(str),Z["RK"].astype(str))
F=pd.read_pickle("/tmp/keirin_feat.pkl").set_index("race_key").reindex(RK)
EXP=DATE<"2026-01-01"
band=PO>=30; sc=np.where(band,PROB,-1.0); top=np.argsort(-sc,axis=1)[:,:5]
v=np.take_along_axis(band,top,1); hit=((top==ACTI[:,None])&v).any(1); npt=v.sum(1)
pay=np.where(hit,AO*100,0.0)
D=pd.DataFrame({"hit":hit,"pay":pay,"pts":npt,"exp":EXP,
  "venue":F.venue_id.astype(str).values,"rtype":F.race_type.astype(str).values,
  "grade":F.grade.astype(str).values,"nl":F.n_lines.values})
for col,label,minn in [("venue","場",300),("rtype","種別",300),("grade","級班",300),("nl","ライン数",300)]:
    a=D[D.exp].groupby(col).agg(n=("hit","size"),hit=("hit","mean"),roi=("pay","sum"),p=("pts","sum"))
    b=D[~D.exp].groupby(col).agg(n=("hit","size"),hit=("hit","mean"),roi=("pay","sum"),p=("pts","sum"))
    a["ROI"]=a.roi/(a.p*100)*100; b["ROI"]=b.roi/(b.p*100)*100
    j=a.join(b,lsuffix="_探",rsuffix="_確").query(f"n_探>={minn} and n_確>={minn//3}")
    if len(j)<4: continue
    r1=spearmanr(j.hit_探,j.hit_確).statistic; r2=spearmanr(j.ROI_探,j.ROI_確).statistic
    print(f"■ {label}（{len(j)}カテゴリ）: 探索↔確認の順位相関  的中率 {r1:+.3f} / ROI {r2:+.3f}")
    if col in ("venue","rtype"):
        t=j.assign(的中探=(j.hit_探*100).round(2),的中確=(j.hit_確*100).round(2),
                   ROI探=j.ROI_探.round(1),ROI確=j.ROI_確.round(1))
        print(t.sort_values("ROI_確",ascending=False)[["n_探","n_確","的中探","的中確","ROI探","ROI確"]].head(6).to_string())
        print("   ...ワースト3:")
        print(t.sort_values("ROI_確")[["n_探","n_確","的中探","的中確","ROI探","ROI確"]].head(3).to_string())
    print()
