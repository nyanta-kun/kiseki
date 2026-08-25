"""軸2差し替えを**本番の 9C の買い方**（三連複2軸流し・ゲート込み）で採点する。

母集団は両腕で完全に同一（ゲートは現行の p3上位2車の較正合計で判定する＝本番と同じ）。
ROI は §29 と同じ土俵で**均等配分**。本番はダッチングなので目安。
"""
import sys, argparse, numpy as np, pandas as pd, lightgbm as lgb
sys.path.insert(0,'.')
from src.database import get_connection
from src.p3_calibration import calibrated_p3_sum_top2
from src.strategy_wt import RANK_9C_P3_SUM_MIN, RANK_9C_LEG_P3_MIN, RANK_9C_LEGS_MIN

ap=argparse.ArgumentParser(); ap.add_argument("--swap",action="store_true"); A=ap.parse_args()
D=pd.read_pickle("/tmp/pair9_rows.pkl")
D9=D[D["ne"]==9]
FEAT=["p3","pw","p3_a1","pw_a1","p3_rank","p3_gap","p3_prod","same_line","lpos_diff",
      "c_lpos","c_lsize","c_lead","c_solo","c_style","c_rp","c_rp_rel","a1_lpos","a1_lsize",
      "a1_lead","a1_solo","a1_style","a1_rp_rel","rp_gap","frame_diff","n_lines","n_ent",
      "n_senko","cg","gr"]
SPLIT="2026-01-01"
tr,te=(D[D.date>=SPLIT],D9[D9.date<SPLIT]) if A.swap else (D[D.date<SPLIT],D9[D9.date>=SPLIT])
ps=[]
for s in range(3):
    m=lgb.LGBMClassifier(objective="binary",n_estimators=400,learning_rate=0.05,num_leaves=31,
        min_child_samples=40,subsample=0.8,colsample_bytree=0.8,random_state=42+s*59,
        deterministic=True,force_row_wise=True,verbose=-1)
    m.fit(tr[FEAT],tr["y"]); ps.append(m.predict_proba(te[FEAT])[:,1])
te=te.copy(); te["pj"]=np.mean(ps,axis=0)

keys=sorted(te.rk.unique()); odds={}; fins={}; rmeta={}; p3all={}
with get_connection() as c:
    for i in range(0,len(keys),500):
        ch=keys[i:i+500]; ph=",".join("?"*len(ch))
        for r in c.execute(f"SELECT race_key,combination,odds_value FROM wt_odds WHERE bet_type='trio' AND race_key IN ({ph})",ch):
            odds.setdefault(r["race_key"],{})[r["combination"]]=float(r["odds_value"])
        for r in c.execute(f"SELECT race_key,frame_no,finish_order FROM wt_entries WHERE race_key IN ({ph})",ch):
            fins.setdefault(r["race_key"],{})[int(r["frame_no"])]=r["finish_order"]
        for r in c.execute(f"SELECT race_key,race_type,cup_grade FROM wt_races WHERE race_key IN ({ph})",ch):
            rmeta[r["race_key"]]=(r["race_type"],r["cup_grade"])
for rk,g in te.groupby("rk"):
    d=dict(zip(g.f.astype(int),g.p3.astype(float))); d[int(g.a1.iloc[0])]=float(g.p3_a1.iloc[0])
    p3all[rk]=d

rows=[]
for rk,g in te.groupby("rk"):
    p3=p3all[rk]; rt,cg=rmeta.get(rk,(None,None))
    gate=calibrated_p3_sum_top2(p3,rt,cg)
    if gate is None or gate < RANK_9C_P3_SUM_MIN: continue
    fo=fins.get(rk,{}); win={f for f,o in fo.items() if o and 1<=int(o)<=3}
    if len(win)!=3: continue
    grid=odds.get(rk) or {}
    a1=int(g.a1.iloc[0])
    for arm,a2 in (("現行",int(g.loc[g.p3_rank.idxmin()].f)), ("新",int(g.loc[g.pj.idxmax()].f))):
        others=[f for f in p3 if f not in (a1,a2)]
        legs=[f for f in sorted(others,key=lambda x:(-p3[x],x)) if p3[f]>=RANK_9C_LEG_P3_MIN]
        if len(legs)<RANK_9C_LEGS_MIN: continue
        n=len(legs); stake=10000/n
        hit=0; pay=0.0
        for L in legs:
            comb="=".join(map(str,sorted((a1,a2,L))))
            if {a1,a2,L}==win:
                hit=1; pay=stake*grid.get(comb,0.0)
        rows.append(dict(rk=rk,arm=arm,n=n,hit=hit,inv=10000,pay=pay,
                         both=int(a1 in win and a2 in win)))
R=pd.DataFrame(rows)
piv=R.pivot_table(index="rk",columns="arm",values=["hit","pay","both","n"],aggfunc="first").dropna()
print(f"\n=== 本番の9Cの買い方で採点（swap={A.swap}） 母集団 {len(piv):,}R ===")
rng=np.random.default_rng(3)
for k,lab in (("both","二軸そろい"),("hit","的中"),):
    a=piv[(k,"現行")].values; b=piv[(k,"新")].values; d=b-a
    bs=[d[rng.integers(0,len(d),len(d))].mean()*100 for _ in range(3000)]
    print(f"  {lab}: 現行 {a.mean()*100:5.2f}%  新 {b.mean()*100:5.2f}%  Δ{d.mean()*100:+5.2f}pt "
          f"CI[{np.percentile(bs,2.5):+.2f},{np.percentile(bs,97.5):+.2f}]")
for arm in ("現行","新"):
    pay=piv[("pay",arm)].values; inv=10000*len(pay)
    bs=[pay[rng.integers(0,len(pay),len(pay))].sum()/inv*100 for _ in range(3000)]
    nz=pay[pay>0]
    print(f"  ROI {arm}: {pay.sum()/inv*100:6.2f}% CI[{np.percentile(bs,2.5):.1f},{np.percentile(bs,97.5):.1f}] "
          f"平均点数 {piv[('n',arm)].mean():.2f}  的中時払戻中央 {int(np.median(nz)) if len(nz) else 0:,}円")
