"""9C のレース選別を「上位2車の p3 合計」から「二軸の同時確率」へ替える（件数を揃えて比較）。

現行ゲート: 較正後 p3_sum_top2 >= 1.30（＝周辺確率の和）
新ゲート  : ペアモデルの P(軸1と軸2がそろって3着内) の上位（**同じ件数**に揃える）
"""
import sys, argparse, numpy as np, pandas as pd, lightgbm as lgb
sys.path.insert(0,'.')
from src.database import get_connection
from src.p3_calibration import calibrated_p3_sum_top2
from src.strategy_wt import RANK_9C_P3_SUM_MIN, RANK_9C_LEG_P3_MIN, RANK_9C_LEGS_MIN

ap=argparse.ArgumentParser(); ap.add_argument("--swap",action="store_true"); A=ap.parse_args()
D=pd.read_pickle("/tmp/pair9_rows.pkl"); D9=D[D["ne"]==9]
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

keys=sorted(te.rk.unique()); odds={}; fins={}; rmeta={}
with get_connection() as c:
    for i in range(0,len(keys),500):
        ch=keys[i:i+500]; ph=",".join("?"*len(ch))
        for r in c.execute(f"SELECT race_key,combination,odds_value FROM wt_odds WHERE bet_type='trio' AND race_key IN ({ph})",ch):
            odds.setdefault(r["race_key"],{})[r["combination"]]=float(r["odds_value"])
        for r in c.execute(f"SELECT race_key,frame_no,finish_order FROM wt_entries WHERE race_key IN ({ph})",ch):
            fins.setdefault(r["race_key"],{})[int(r["frame_no"])]=r["finish_order"]
        for r in c.execute(f"SELECT race_key,race_type,cup_grade,race_date FROM wt_races WHERE race_key IN ({ph})",ch):
            rmeta[r["race_key"]]=(r["race_type"],r["cup_grade"],str(r["race_date"]))

recs=[]
for rk,g in te.groupby("rk"):
    p3=dict(zip(g.f.astype(int),g.p3.astype(float))); a1=int(g.a1.iloc[0]); p3[a1]=float(g.p3_a1.iloc[0])
    rt,cg,dt=rmeta.get(rk,(None,None,None))
    fo=fins.get(rk,{}); win={f for f,o in fo.items() if o and 1<=int(o)<=3}
    if len(win)!=3: continue
    grid=odds.get(rk) or {}
    row=g.loc[g.pj.idxmax()]; a2n=int(row.f); pj=float(row.pj)
    a2c=int(g.loc[g.p3_rank.idxmin()].f)
    gate_cur=calibrated_p3_sum_top2(p3,rt,cg) or 0.0
    out=dict(rk=rk,date=dt,gate_cur=gate_cur,pj=pj)
    for arm,a2 in (("cur",a2c),("new",a2n)):
        others=[f for f in p3 if f not in (a1,a2)]
        legs=[f for f in sorted(others,key=lambda x:(-p3[x],x)) if p3[f]>=RANK_9C_LEG_P3_MIN]
        ok=len(legs)>=RANK_9C_LEGS_MIN
        hit=0; pay=0.0
        if ok:
            st=10000/len(legs)
            for L in legs:
                if {a1,a2,L}==win:
                    hit=1; pay=st*grid.get("=".join(map(str,sorted((a1,a2,L)))),0.0)
        out[f"{arm}_ok"]=ok; out[f"{arm}_hit"]=hit; out[f"{arm}_pay"]=pay
        out[f"{arm}_both"]=int(a1 in win and a2 in win); out[f"{arm}_n"]=len(legs)
    recs.append(out)
R=pd.DataFrame(recs)
nd=R.date.nunique()
rng=np.random.default_rng(5)
def rep(sub,lab,arm):
    s=sub[sub[f"{arm}_ok"]]
    if len(s)<50: print(f"  {lab:34s} 件数不足 {len(s)}"); return
    pay=s[f"{arm}_pay"].values; inv=10000*len(s)
    b=[pay[rng.integers(0,len(pay),len(pay))].sum()/inv*100 for _ in range(2500)]
    print(f"  {lab:34s} {len(s):5d}R {len(s)/nd:5.2f}件/日 二軸 {s[f'{arm}_both'].mean()*100:5.2f}% "
          f"的中 {s[f'{arm}_hit'].mean()*100:5.2f}% ROI {pay.sum()/inv*100:6.2f}% CI[{np.percentile(b,2.5):.0f},{np.percentile(b,97.5):.0f}]")

cur_sel = R[R.gate_cur>=RANK_9C_P3_SUM_MIN]
k = len(cur_sel)
new_sel = R.nlargest(k, "pj")
print(f"\n=== 9C のレース選別（swap={A.swap}） 全 {len(R):,}R / {nd}日 ===")
print(f"  現行ゲート通過 {k:,}R（{k/len(R)*100:.1f}%）／新ゲートは同件数に揃える")
print("\n[A] 現行ゲート × 現行軸2（＝いまの本番）")
rep(cur_sel,"p3_sum>=1.30 × p3 2位","cur")
print("[B] 現行ゲート × 新軸2")
rep(cur_sel,"p3_sum>=1.30 × 同時確率","new")
print("[C] 新ゲート（同時確率上位・同件数） × 新軸2")
rep(new_sel,"同時確率 上位 × 同時確率","new")
print("[D] 新ゲート × 現行軸2（ゲートだけ替える）")
rep(new_sel,"同時確率 上位 × p3 2位","cur")
print("\n=== 件数を変えたときの新ゲート（新軸2）===")
for frac in (0.15,0.25,0.35,0.50,0.70,1.00):
    rep(R.nlargest(int(len(R)*frac),"pj"), f"同時確率 上位{int(frac*100)}%","new")
print("\n=== 参考: 現行ゲートを厳しくした場合（現行軸2）===")
for th in (1.30,1.35,1.40,1.45):
    rep(R[R.gate_cur>=th], f"p3_sum>={th}","cur")
