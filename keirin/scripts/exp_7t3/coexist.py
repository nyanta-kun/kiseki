"""7T1(決勝×別ライン) と 新枠(決勝×30倍+5点) の棲み分け。"""
import pickle, sys, numpy as np, pandas as pd
sys.path.insert(0,'.'); sys.path.insert(0,'/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/7bc12223-92ab-4766-81d1-18bc01d1b207/scratchpad')
from src.strategy_wt import rank_7t1_select, rank_7t1_stakes
from tfprob import blend_pl
rng=np.random.default_rng(606)
BOARD=pickle.load(open("/tmp/keirin_tf_board.pkl","rb"))
d=pickle.load(open("/tmp/keirin_upset_ds.pkl","rb"))
E=d["E"].merge(d["pred"],on=["race_key","frame_no"],how="inner")
F=pd.read_pickle("/tmp/keirin_feat.pkl").set_index("race_key")
Z=np.load("/tmp/design_mat.npz",allow_pickle=True)
RK=Z["RK"].astype(str);ACTI=Z["ACTI"];AO=Z["AO"].astype(float);PERM=Z["PERM"]
act={rk:tuple(PERM[i]+1) for rk,i in zip(RK,ACTI)}; ao=dict(zip(RK,AO))
CANON=list(BOARD[list(BOARD)[0]][0]); KES={"決勝","チャレンジ決勝"}
tgt=set(rk for rk in BOARD if str(F.race_type.get(rk,"")) in KES and rk in act)
rows=[]
for rk,g in E[E.race_key.isin(tgt)].groupby("race_key"):
    if len(g)!=7: continue
    cars=g.frame_no.astype(int).tolist()
    p3=dict(zip(cars,g.pp3.astype(float))); pw=dict(zip(cars,g.ppw.astype(float)))
    po=dict(zip(CANON,np.asarray(BOARD[rk][1],float)))
    o3=sorted(cars,key=lambda c:-p3[c]); lg=dict(zip(cars,g.line_group))
    cross=not(lg.get(o3[0]) is not None and lg.get(o3[0])==lg.get(o3[1]))
    sel=rank_7t1_select(p3,pw,po)
    P=blend_pl(cars,pw,p3,(1,.5,0))
    band={k:v for k,v in po.items() if v>=30}
    legs5=sorted(band,key=lambda k:-P.get(k,0))[:5] if band else []
    a=act[rk]; UNIT=2000
    r=dict(rk=rk,date=str(F.race_date.get(rk)),cross=cross,
      t1_ok=bool(sel), t1_inv=0.0,t1_pay=0.0,t1_hit=0,t1_n=0,
      n5_ok=bool(legs5), n5_inv=len(legs5)*UNIT,
      n5_pay=(ao[rk]*UNIT if a in set(legs5) else 0.0), n5_hit=int(a in set(legs5)))
    if sel:
        st=rank_7t1_stakes(sel[2]); r["t1_inv"]=sum(st.values()); r["t1_n"]=len(sel[2])
        s=" -".join([]) 
        key="-".join(map(str,a))
        if key in st: r["t1_pay"]=ao[rk]*st[key]; r["t1_hit"]=1
    rows.append(r)
D=pd.DataFrame(rows); nd=F.race_date.nunique()
def rep(sub,lbl,pre):
    R=len(sub); inv=sub[f"{pre}_inv"].sum(); pay=sub[f"{pre}_pay"].values; h=int(sub[f"{pre}_hit"].sum())
    if R==0 or inv==0: return
    b=[pay[rng.integers(0,R,R)].sum()/inv*100 for _ in range(1500)]
    print(f"  {lbl:<34} {R:5d}R {R/nd:5.2f}件/日 的中 {h:3d}件({h/R*100:5.2f}%) ROI {pay.sum()/inv*100:6.1f}% "
          f"CI[{np.percentile(b,2.5):.0f},{np.percentile(b,97.5):.0f}] 中央 {int(np.median(pay[pay>0])) if h else 0:>8,}円")
print(f"決勝 {len(D):,}R / 全 {nd}日")
print("\n■ 案A: 7T1=決勝×別ライン / 新枠=決勝×同ラインのみ（排他）")
rep(D[D.cross&D.t1_ok],"7T1（決勝×別ライン・高配当2.4点）","t1")
rep(D[~D.cross],"新枠（決勝×同ライン・30倍+5点）","n5")
print("\n■ 案B: 新枠が決勝ぜんぶを取る（7T1 は決勝から撤退し準決勝等へ）")
rep(D,"新枠（決勝すべて・30倍+5点）","n5")
print("\n■ 参考: 新枠を別ライン/同ラインで割ると")
rep(D[D.cross],"新枠（決勝×別ライン）","n5")
rep(D[~D.cross],"新枠（決勝×同ライン）","n5")
