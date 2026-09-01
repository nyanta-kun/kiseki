"""9車で三連単購入モデルが成立するかの可否判定。
🔴 予測オッズモデル(odds_tf_n9)が無いので **確定オッズで帯を切る＝上限の見積もり**。
   ここで成立しなければ odds_tf_n9 を作る意味は無い。"""
import glob, pickle, sys, itertools, numpy as np, pandas as pd
sys.path.insert(0,'.'); sys.path.insert(0,'scripts/exp_7t3')
from tfprob import blend_pl
from src.database import get_connection
from src.result_top3 import winning_trifectas
rng=np.random.default_rng(1313)

fr=[]
for f in sorted(glob.glob("data/exp_cache/wf_preds9_*.pkl")):
    d=pickle.load(open(f,"rb")); fr.append(d[["race_key","frame_no","pp3","ppw"]])
P=pd.concat(fr,ignore_index=True).drop_duplicates(["race_key","frame_no"])
print(f"9車 vintage 予測: {P.race_key.nunique():,}R")
keys=sorted(P.race_key.unique())
meta={}; fins={}; odds={}
with get_connection() as c:
    for i in range(0,len(keys),700):
        ch=keys[i:i+700]; ph=",".join("?"*len(ch))
        for r in c.execute(f"SELECT race_key,race_date,race_type,n_entries FROM wt_races WHERE race_key IN ({ph})",ch).fetchall():
            meta[r["race_key"]]=(str(r["race_date"]),str(r["race_type"]),int(r["n_entries"] or 0))
        for r in c.execute(f"SELECT race_key,frame_no,finish_order,line_group FROM wt_entries WHERE race_key IN ({ph})",ch).fetchall():
            fins.setdefault(r["race_key"],[]).append((r["finish_order"],int(r["frame_no"]),r["line_group"]))
        for r in c.execute(f"SELECT race_key,combination,odds_value FROM wt_odds WHERE bet_type='trifecta' AND race_key IN ({ph})",ch).fetchall():
            odds.setdefault(r["race_key"],{})[tuple(int(x) for x in r["combination"].split("-"))]=float(r["odds_value"])
rows=[]
for rk,g in P.groupby("race_key"):
    m=meta.get(rk); grid=odds.get(rk); fi=fins.get(rk)
    if not m or m[2]!=9 or not grid or not fi: continue
    top=[(int(o),f) for o,f,_ in fi if o and int(o)>=1]
    if len([o for o,_ in top if o<=3])<3: continue
    ws=set(winning_trifectas(sorted([(o,f) for o,f in top])))
    cars=g.frame_no.astype(int).tolist()
    if len(cars)!=9: continue
    pw=dict(zip(cars,g.ppw.astype(float))); p3=dict(zip(cars,g.pp3.astype(float)))
    Pb=blend_pl(cars,pw,p3,(1,.5,0))
    lg={f:l for _,f,l in fi}
    o3=sorted(cars,key=lambda c:-p3[c])
    cross=not(lg.get(o3[0]) is not None and lg.get(o3[0])==lg.get(o3[1]))
    for lo in (30,50,100):
        band=[k for k,v in grid.items() if v>=lo]
        legs=sorted(band,key=lambda k:-Pb.get(k,0))[:5]
        if not legs: continue
        hk=next((k for k in legs if k in ws),None)
        rows.append(dict(rk=rk,date=m[0],rtype=m[1],cross=cross,lo=lo,n=len(legs),
            hit=int(hk is not None),pay=(grid[hk]*100 if hk else 0.0)))
D=pd.DataFrame(rows); D.to_pickle("/tmp/nine.pkl")
nd=D.date.nunique(); print(f"評価対象 {D.rk.nunique():,}R / {nd}日\n")
def rep(s,l):
    R=len(s)
    if R<80: print(f"  {l:<26} {R:5d}R  ← 件数不足"); return
    inv=s.n.sum()*100; pay=s.pay.values; h=int(s.hit.sum())
    b=[pay[rng.integers(0,R,R)].sum()/inv*100 for _ in range(1200)]
    print(f"  {l:<26} {R:5d}R {R/nd:5.2f}件/日 的中 {h/R*100:5.2f}% 週{h/R*(R/nd)*7:5.2f}ヒット "
          f"ROI {pay.sum()/inv*100:6.1f}% CI[{np.percentile(b,2.5):.0f},{np.percentile(b,97.5):.0f}] "
          f"中央 {int(np.median(pay[pay>0])*20) if h else 0:>8,}円")
for lo in (30,50,100):
    s=D[D.lo==lo]; print(f"■ 確定{lo}倍以上 × 確率上位5点（9車）")
    rep(s,"全9車レース")
    for t in ["決勝","準決勝","特選","選抜","一予選","二予選","初特選","特秀","一般"]:
        rep(s[s.rtype==t],f"  {t}")
    print()
