"""全網羅の前提で「外れている群」を定義し、そこで軸2の条件付きモデルが効くかを測る。"""
import sys, numpy as np, pandas as pd, lightgbm as lgb
sys.path.insert(0,'.')
from src.database import get_connection
from src.strategy_wt import RANK_9C_LEG_P3_MIN, RANK_9C_LEGS_MIN

C = pd.read_pickle("/tmp/cov9.pkl")
C["win_"] = C.date < "2026-01-01"
rng = np.random.default_rng(31)

# --- 外れ群 / 当たり群の定義（両窓で一致した3軸だけを使う）---
LOSE_TYPE = {"一般","特秀","特選"}
WIN_TYPE  = {"一予選","二予選"}
C["grp"] = np.where(C.rtype.isin(WIN_TYPE) & (C.day<=2), "当たり群(予選系×1-2日目)",
            np.where(C.rtype.isin(LOSE_TYPE) | (C.nl>=5), "外れ群(番組編成/5ライン)", "中間"))
print("=== 全網羅 4,593R を3群に分ける ===")
for g,s in C.groupby("grp"):
    a,b = s[s.win_], s[~s.win_]
    print(f"  {g:<26} {len(s):5d}R ({len(s)/len(C)*100:4.1f}%) 的中 {s.hit.mean()*100:5.1f}% "
          f"ROI {s.pay.sum()/(10000*len(s))*100:6.1f}%  | 探索 {a.hit.mean()*100:5.1f}%/{a.pay.sum()/(10000*len(a))*100:5.1f}% "
          f"| 確認 {b.hit.mean()*100:5.1f}%/{b.pay.sum()/(10000*len(b))*100:5.1f}%")
print("\n  二軸が別ラインになる率 / 群別")
for g,s in C.groupby("grp"):
    print(f"    {g:<26} 別ライン {100-s.same.mean()*100:5.1f}%  ゲート通過 {(s.gate>=1.30).mean()*100:5.1f}%")

# --- 軸2ペアモデルを全網羅母集団へ当てる ---
D = pd.read_pickle("/tmp/pair9_rows.pkl")
FEAT=["p3","pw","p3_a1","pw_a1","p3_rank","p3_gap","p3_prod","same_line","lpos_diff",
      "c_lpos","c_lsize","c_lead","c_solo","c_style","c_rp","c_rp_rel","a1_lpos","a1_lsize",
      "a1_lead","a1_solo","a1_style","a1_rp_rel","rp_gap","frame_diff","n_lines","n_ent",
      "n_senko","cg","gr"]
D9 = D[D["ne"]==9]
out=[]
for swap in (False, True):
    SP="2026-01-01"
    tr,te = (D[D.date>=SP], D9[D9.date<SP]) if swap else (D[D.date<SP], D9[D9.date>=SP])
    ps=[]
    for s in range(3):
        m=lgb.LGBMClassifier(objective="binary",n_estimators=400,learning_rate=0.05,num_leaves=31,
            min_child_samples=40,subsample=0.8,colsample_bytree=0.8,random_state=42+s*59,
            deterministic=True,force_row_wise=True,verbose=-1)
        m.fit(tr[FEAT],tr["y"]); ps.append(m.predict_proba(te[FEAT])[:,1])
    t=te.copy(); t["pj"]=np.mean(ps,axis=0); t["swap"]=swap; out.append(t)
T=pd.concat(out,ignore_index=True)

keys=sorted(T.rk.unique()); odds={}; fins={}
with get_connection() as c:
    for i in range(0,len(keys),500):
        ch=keys[i:i+500]; ph=",".join("?"*len(ch))
        for r in c.execute(f"SELECT race_key,combination,odds_value FROM wt_odds WHERE bet_type='trio' AND race_key IN ({ph})",ch):
            odds.setdefault(r["race_key"],{})[r["combination"]]=float(r["odds_value"])
        for r in c.execute(f"SELECT race_key,frame_no,finish_order FROM wt_entries WHERE race_key IN ({ph})",ch):
            fins.setdefault(r["race_key"],{})[int(r["frame_no"])]=r["finish_order"]

recs=[]
for rk,g in T.groupby("rk"):
    p3=dict(zip(g.f.astype(int),g.p3.astype(float))); a1=int(g.a1.iloc[0]); p3[a1]=float(g.p3_a1.iloc[0])
    fo=fins.get(rk,{}); win={f for f,o in fo.items() if o and 1<=int(o)<=3}
    if len(win)!=3: continue
    grid=odds.get(rk) or {}
    rec=dict(rk=rk)
    for arm,a2 in (("cur",int(g.loc[g.p3_rank.idxmin()].f)), ("new",int(g.loc[g.pj.idxmax()].f))):
        order=sorted([f for f in p3 if f not in (a1,a2)],key=lambda x:(-p3[x],x))
        legs=[f for f in order if p3[f]>=RANK_9C_LEG_P3_MIN] or []
        if len(legs)<RANK_9C_LEGS_MIN: legs=order[:RANK_9C_LEGS_MIN]
        st=10000/len(legs); h=0; pay=0.0
        for L in legs:
            if {a1,a2,L}==win:
                h=1; pay=st*grid.get("=".join(map(str,sorted((a1,a2,L)))),0.0)
        rec[f"{arm}_hit"]=h; rec[f"{arm}_pay"]=pay; rec[f"{arm}_both"]=int(a1 in win and a2 in win)
    recs.append(rec)
R=pd.DataFrame(recs).merge(C[["rk","grp","rtype","nl","day","gate","win_","same"]],on="rk")
print(f"\n=== 全網羅で軸2を差し替える（対象 {len(R):,}R・両窓を結合＝各レースは学習外）===")
def rep(s,lab):
    if len(s)<80: print(f"  {lab:<26} {len(s):5d}R ← 件数不足"); return
    for k,nm in (("both","二軸"),("hit","的中")):
        a=s[f"cur_{k}"].values; b=s[f"new_{k}"].values; d=b-a
        bs=[d[rng.integers(0,len(d),len(d))].mean()*100 for _ in range(2500)]
        print(f"  {lab:<26} {len(s):5d}R {nm} {a.mean()*100:5.1f}%→{b.mean()*100:5.1f}% "
              f"Δ{d.mean()*100:+5.2f}pt CI[{np.percentile(bs,2.5):+.2f},{np.percentile(bs,97.5):+.2f}]")
    ri=lambda v: v.sum()/(10000*len(v))*100
    print(f"  {'':<26}       ROI {ri(s.cur_pay):6.1f}% → {ri(s.new_pay):6.1f}%")
rep(R,"全網羅")
for g,s in R.groupby("grp"): rep(s,g)
print()
rep(R[R.gate>=1.30],"ゲート通過のみ")
rep(R[R.gate< 1.30],"ゲート不通過(穴埋め)のみ")
