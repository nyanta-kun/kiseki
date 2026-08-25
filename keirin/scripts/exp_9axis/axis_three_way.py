"""3すくみ比較: p3上位2車 / 本番の穴埋め（ライン組み替え） / ペアモデル。

🔴 穴埋め経路は既に `_axes()` でライン組み替えをしている。ベースラインを
   p3上位2車に置くと**本番より弱い相手と比べる**ことになる（CLAUDE.md 検証の作法 #1）。
"""
import sys, numpy as np, pandas as pd, lightgbm as lgb
sys.path.insert(0,'.')
from src.database import get_connection
from src.strategy_wt import RANK_9C_LEG_P3_MIN, RANK_9C_LEGS_MIN

C = pd.read_pickle("/tmp/cov9.pkl")
D = pd.read_pickle("/tmp/pair9_rows.pkl"); D9 = D[D["ne"]==9]
FEAT=["p3","pw","p3_a1","pw_a1","p3_rank","p3_gap","p3_prod","same_line","lpos_diff",
      "c_lpos","c_lsize","c_lead","c_solo","c_style","c_rp","c_rp_rel","a1_lpos","a1_lsize",
      "a1_lead","a1_solo","a1_style","a1_rp_rel","rp_gap","frame_diff","n_lines","n_ent",
      "n_senko","cg","gr"]
out=[]
for swap in (False, True):
    SP="2026-01-01"
    tr,te=(D[D.date>=SP],D9[D9.date<SP]) if swap else (D[D.date<SP],D9[D9.date>=SP])
    ps=[]
    for s in range(3):
        m=lgb.LGBMClassifier(objective="binary",n_estimators=400,learning_rate=0.05,num_leaves=31,
            min_child_samples=40,subsample=0.8,colsample_bytree=0.8,random_state=42+s*59,
            deterministic=True,force_row_wise=True,verbose=-1)
        m.fit(tr[FEAT],tr["y"]); ps.append(m.predict_proba(te[FEAT])[:,1])
    t=te.copy(); t["pj"]=np.mean(ps,axis=0); out.append(t)
T=pd.concat(out,ignore_index=True)

keys=sorted(T.rk.unique()); odds={}; ent={}
with get_connection() as c:
    for i in range(0,len(keys),500):
        ch=keys[i:i+500]; ph=",".join("?"*len(ch))
        for r in c.execute(f"SELECT race_key,combination,odds_value FROM wt_odds WHERE bet_type='trio' AND race_key IN ({ph})",ch):
            odds.setdefault(r["race_key"],{})[r["combination"]]=float(r["odds_value"])
        for r in c.execute(f"SELECT race_key,frame_no,finish_order,line_group,line_size,is_line_leader FROM wt_entries WHERE race_key IN ({ph})",ch):
            ent.setdefault(r["race_key"],[]).append(dict(r))

def axes_marquee(order, E):
    """本番 `_axes()` の再現（単騎は先頭に数えない・同ライン最上位へ差し替え）。"""
    a1,a2 = order[0],order[1]
    lead=lambda n: bool(E[n]["is_line_leader"] and (E[n]["line_size"] or 1)>1)
    def same(head):
        g=E[head]["line_group"]
        for n in order:
            if n!=head and E[n]["line_group"]==g: return n
        return None
    if lead(a1):
        p=same(a1)
        if p is not None: return a1,p
    elif lead(a2):
        p=same(a2)
        if p is not None: return a2,p
    return a1,a2

recs=[]
for rk,g in T.groupby("rk"):
    es=ent.get(rk)
    if not es: continue
    E={e["frame_no"]:e for e in es}
    fo={e["frame_no"]:e["finish_order"] for e in es}
    win={f for f,o in fo.items() if o and 1<=int(o)<=3}
    if len(win)!=3: continue
    p3=dict(zip(g.f.astype(int),g.p3.astype(float))); a1=int(g.a1.iloc[0]); p3[a1]=float(g.p3_a1.iloc[0])
    order=sorted(p3,key=lambda f:(-p3[f],f))
    grid=odds.get(rk) or {}
    arms={"p3上位2車":(order[0],order[1]),
          "本番の穴埋め(組み替え)":axes_marquee(order,E),
          "ペアモデル":(a1,int(g.loc[g.pj.idxmax()].f))}
    rec=dict(rk=rk)
    for nm,(x1,x2) in arms.items():
        o2=sorted([f for f in p3 if f not in (x1,x2)],key=lambda f:(-p3[f],f))
        legs=[f for f in o2 if p3[f]>=RANK_9C_LEG_P3_MIN]
        if len(legs)<RANK_9C_LEGS_MIN: legs=o2[:RANK_9C_LEGS_MIN]
        st=10000/len(legs); h=0; pay=0.0
        for L in legs:
            if {x1,x2,L}==win:
                h=1; pay=st*grid.get("=".join(map(str,sorted((x1,x2,L)))),0.0)
        rec[nm+"_hit"]=h; rec[nm+"_pay"]=pay; rec[nm+"_both"]=int(x1 in win and x2 in win)
    recs.append(rec)
R=pd.DataFrame(recs).merge(C[["rk","grp" if "grp" in C else "rtype","rtype","nl","day","gate"]] if False else C[["rk","rtype","nl","day","gate"]],on="rk")
LOSE={"一般","特秀","特選"}; WIN={"一予選","二予選"}
R["grp"]=np.where(R.rtype.isin(WIN)&(R.day<=2),"当たり群(予選系×1-2日目)",
        np.where(R.rtype.isin(LOSE)|(R.nl>=5),"外れ群(番組編成/5ライン)","中間"))
rng=np.random.default_rng(41)
ARMS=["p3上位2車","本番の穴埋め(組み替え)","ペアモデル"]
def rep(s,lab):
    if len(s)<80: print(f"  {lab}: 件数不足"); return
    print(f"  ── {lab}  n={len(s):,}R")
    for a in ARMS:
        print(f"     {a:<22} 二軸 {s[a+'_both'].mean()*100:5.1f}%  的中 {s[a+'_hit'].mean()*100:5.1f}%  "
              f"ROI {s[a+'_pay'].sum()/(10000*len(s))*100:6.1f}%")
    base="本番の穴埋め(組み替え)"
    for a in ("ペアモデル",):
        for k,nm in (("_both","二軸"),("_hit","的中")):
            d=(s[a+k]-s[base+k]).values
            b=[d[rng.integers(0,len(d),len(d))].mean()*100 for _ in range(2500)]
            print(f"     Δ({a} − 本番) {nm} {d.mean()*100:+5.2f}pt CI[{np.percentile(b,2.5):+.2f},{np.percentile(b,97.5):+.2f}]")
print("=== 3すくみ（全網羅・vintage）===")
rep(R,"全網羅")
for g,s in R.groupby("grp"): rep(s,g)
print()
rep(R[R.gate<1.30],"ゲート不通過＝実際に穴埋めが出る母集団")
