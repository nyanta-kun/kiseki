"""決勝レースで別ライン制約を外したらどうなるか + 2026-04〜08 の月次。"""
import pickle, sys, numpy as np, pandas as pd
sys.path.insert(0,'.')
from src.strategy_wt import rank_7t1_select, rank_7t1_stakes
rng=np.random.default_rng(505)
BOARD=pickle.load(open("/tmp/keirin_tf_board.pkl","rb"))
d=pickle.load(open("/tmp/keirin_upset_ds.pkl","rb"))
E=d["E"].merge(d["pred"],on=["race_key","frame_no"],how="inner")
F=pd.read_pickle("/tmp/keirin_feat.pkl").set_index("race_key")
Z=np.load("/tmp/design_mat.npz",allow_pickle=True)
RK=Z["RK"].astype(str); ACTI=Z["ACTI"]; AO=Z["AO"].astype(float); PERM=Z["PERM"]
act={rk:"-".join(map(str,PERM[i]+1)) for rk,i in zip(RK,ACTI)}; ao=dict(zip(RK,AO))
CANON=list(BOARD[list(BOARD)[0]][0])
KES={"決勝","チャレンジ決勝"}
tgt=[rk for rk in BOARD if str(F.race_type.get(rk,"")) in KES and rk in act]
print(f"決勝レース {len(tgt):,}R")
rows=[]
E=E[E.race_key.isin(set(tgt))]
for rk,g in E.groupby("race_key"):
    if len(g)!=7: continue
    cars=g.frame_no.astype(int).tolist()
    p3=dict(zip(cars,g.pp3.astype(float))); pw=dict(zip(cars,g.ppw.astype(float)))
    po=dict(zip(CANON,np.asarray(BOARD[rk][1],float)))
    o3=sorted(cars,key=lambda c:-p3[c]); lg=dict(zip(cars,g.line_group))
    cross = not (lg.get(o3[0]) is not None and lg.get(o3[0])==lg.get(o3[1]))
    sel=rank_7t1_select(p3,pw,po)
    if not sel: continue
    legs=sel[2]; st=rank_7t1_stakes(legs)
    rows.append(dict(rk=rk,date=str(F.race_date.get(rk)),cross=cross,n=len(legs),
      inv=sum(st.values()), pay=(ao[rk]*st[act[rk]] if act[rk] in st else 0.0),
      hit=int(act[rk] in st)))
D=pd.DataFrame(rows); nd=D.date.nunique()
def rep(sub,lbl):
    R=len(sub); inv=sub.inv.sum(); pay=sub.pay.values; h=int(sub.hit.sum())
    b=[pay[rng.integers(0,R,R)].sum()/inv*100 for _ in range(1500)]
    print(f"  {lbl:<28} {R:5d}R {R/nd:5.2f}件/日 平均{sub.n.mean():.1f}点 的中 {h:3d}件({h/R*100:5.2f}%) "
          f"ROI {pay.sum()/inv*100:6.1f}% CI[{np.percentile(b,2.5):.0f},{np.percentile(b,97.5):.0f}] "
          f"中央 {int(np.median(pay[pay>0])) if h else 0:>8,}円 最大 {int(pay.max()):>9,}円")
print(f"\n■ 決勝レースでの 7T1 買い方（{nd}日）")
rep(D,"決勝すべて（別ライン制約なし）")
rep(D[D.cross],"決勝×別ライン（現行の条件）")
rep(D[~D.cross],"決勝×同ライン（現行は捨てている）")
print("\n■ 2026-04〜08 の月次（決勝すべて・別ライン制約なし）")
D["月"]=D.date.str[:7]
for m,g in D[D.月>="2026-04"].groupby("月"):
    n2=g.date.nunique(); h=int(g.hit.sum())
    print(f"  {m}: {len(g):3d}R {len(g)/n2:.1f}件/日 的中 {h}件({h/len(g)*100:5.2f}%) ROI {g.pay.sum()/g.inv.sum()*100:6.1f}%")
