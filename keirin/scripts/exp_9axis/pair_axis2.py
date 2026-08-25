"""軸2の条件付きモデル（ペアモデル）— 9車。

軸1 = vintage p3 の1位で固定し、残り候補から「軸1とそろって3着内に入る」確率が
最大の車を軸2に採る。現行（p3 2位）との直接対決。

学習・検定は年をまたぐ独立窓（--swap で逆向き）。予測は必ず vintage
（wf_preds9_*.pkl / wf_preds_*.pkl）を使う。
"""
import sys, glob, pickle, argparse, numpy as np, pandas as pd, lightgbm as lgb
sys.path.insert(0,'.')
from src.database import get_connection

ap = argparse.ArgumentParser(); ap.add_argument("--swap", action="store_true")
ap.add_argument("--seeds", type=int, default=3)
A = ap.parse_args()

def load(pat):
    fr=[]
    for f in sorted(glob.glob(f"data/exp_cache/{pat}")):
        d=pickle.load(open(f,"rb")); fr.append(d[["race_key","frame_no","pp3","ppw"]])
    return pd.concat(fr,ignore_index=True).drop_duplicates(["race_key","frame_no"])

import os
if os.path.exists("/tmp/pair9_rows.pkl"):
    D = pd.read_pickle("/tmp/pair9_rows.pkl"); _cached = True
else:
    _cached = False
if not _cached:
  P = pd.concat([load("wf_preds9_*.pkl"), load("wf_preds_*.pkl")], ignore_index=True)
  keys = sorted(P.race_key.unique())
  meta, ent = {}, {}
  with get_connection() as c:
      for i in range(0,len(keys),700):
          ch=keys[i:i+700]; ph=",".join("?"*len(ch))
          for r in c.execute(f"SELECT race_key,race_date,race_type,n_entries,cup_grade,grade FROM wt_races WHERE race_key IN ({ph})",ch):
              meta[r["race_key"]]=dict(date=str(r["race_date"]),rtype=r["race_type"],
                  ne=int(r["n_entries"] or 0),cg=r["cup_grade"],gr=r["grade"])
          for r in c.execute(f"SELECT race_key,frame_no,finish_order,line_group,line_size,line_pos,"
                             f"is_line_leader,n_lines,style,race_point FROM wt_entries WHERE race_key IN ({ph})",ch):
              ent.setdefault(r["race_key"],[]).append(dict(r))

  STY={"逃":0,"両":1,"追":2}
  rows=[]
  for rk,g in P.groupby("race_key"):
      m=meta.get(rk); es=ent.get(rk)
      if not m or not es or m["ne"] not in (7,9): continue
      fo={e["frame_no"]:e["finish_order"] for e in es}
      if sum(1 for v in fo.values() if v and 1<=int(v)<=3)!=3: continue
      p3=dict(zip(g.frame_no.astype(int),g.pp3.astype(float)))
      pw=dict(zip(g.frame_no.astype(int),g.ppw.astype(float)))
      if len(p3)!=m["ne"]: continue
      E={e["frame_no"]:e for e in es}
      if any(f not in E for f in p3): continue
      order=sorted(p3,key=lambda f:(-p3[f],f))
      a1=order[0]; e1=E[a1]
      t3=lambda f: int(bool(fo.get(f) and 1<=int(fo[f])<=3))
      rpv={f:(E[f]["race_point"] or 0) for f in p3}
      rpmax=max(rpv.values()) or 1
      for f in order[1:]:
          e=E[f]
          same = 1 if (e["line_group"] is not None and e["line_group"]==e1["line_group"]) else 0
          rows.append(dict(rk=rk,date=m["date"],ne=m["ne"],f=f,a1=a1,
              y=t3(a1)*t3(f), y_c=t3(f), a1_top3=t3(a1),
              p3=p3[f], pw=pw[f], p3_a1=p3[a1], pw_a1=pw[a1],
              p3_rank=order.index(f)+1, p3_gap=p3[a1]-p3[f], p3_prod=p3[a1]*p3[f],
              same_line=same,
              lpos_diff=(e["line_pos"] or 0)-(e1["line_pos"] or 0) if same else 0,
              c_lpos=e["line_pos"] or 0, c_lsize=e["line_size"] or 1,
              c_lead=e["is_line_leader"] or 0, c_solo=int((e["line_size"] or 1)==1),
              c_style=STY.get(e["style"],1), c_rp=rpv[f], c_rp_rel=rpv[f]/rpmax,
              a1_lpos=e1["line_pos"] or 0, a1_lsize=e1["line_size"] or 1,
              a1_lead=e1["is_line_leader"] or 0, a1_solo=int((e1["line_size"] or 1)==1),
              a1_style=STY.get(e1["style"],1), a1_rp_rel=rpv[a1]/rpmax,
              rp_gap=rpv[a1]-rpv[f], frame_diff=abs(f-a1),
              n_lines=e["n_lines"] or 0, n_ent=m["ne"],
              n_senko=sum(1 for x in es if x["style"]=="逃"),
              cg=(m["cg"] or 0), gr={"S級":3,"SA混合":3,"A級":2,"L級":1}.get(m["gr"],2)))
  D=pd.DataFrame(rows)
  D.to_pickle("/tmp/pair9_rows.pkl")
n9 = D[D["ne"]==9].rk.nunique()
print(f"行 {len(D):,} / レース {D.rk.nunique():,}（9車 {n9:,}）", flush=True)

FEAT=["p3","pw","p3_a1","pw_a1","p3_rank","p3_gap","p3_prod","same_line","lpos_diff",
      "c_lpos","c_lsize","c_lead","c_solo","c_style","c_rp","c_rp_rel","a1_lpos","a1_lsize",
      "a1_lead","a1_solo","a1_style","a1_rp_rel","rp_gap","frame_diff","n_lines","n_ent",
      "n_senko","cg","gr"]
SPLIT="2026-01-01"
if A.swap: tr=D[D.date>=SPLIT]; te=D[D.date<SPLIT]
else:      tr=D[D.date<SPLIT];  te=D[D.date>=SPLIT]
print(f"学習 {tr.rk.nunique():,}R / 検定 {te.rk.nunique():,}R  (swap={A.swap})", flush=True)

ps=[]
for s in range(A.seeds):
    m=lgb.LGBMClassifier(objective="binary",n_estimators=400,learning_rate=0.05,
        num_leaves=31,min_child_samples=40,subsample=0.8,colsample_bytree=0.8,
        random_state=42+s*59,deterministic=True,force_row_wise=True,verbose=-1)
    m.fit(tr[FEAT],tr["y"]); ps.append(m.predict_proba(te[FEAT])[:,1])
te=te.copy(); te["pj"]=np.mean(ps,axis=0)

rng=np.random.default_rng(9)
def report(sub,lab):
    out=[]
    for rk,g in sub.groupby("rk"):
        cur=g.loc[g.p3_rank.idxmin()]                 # 現行: p3 2位
        new=g.loc[g.pj.idxmax()]                      # 新: 同時確率 最大
        out.append((rk,int(cur.y),int(new.y),int(cur.f==new.f),int(cur.same_line),int(new.same_line),int(cur.a1_top3)))
    O=pd.DataFrame(out,columns=["rk","cur","new","same_pick","cur_sl","new_sl","a1"])
    n=len(O)
    d=O.new.values-O.cur.values
    b=[d[rng.integers(0,n,n)].mean()*100 for _ in range(3000)]
    print(f"  {lab}: n={n:,}R  現行二軸 {O.cur.mean()*100:5.2f}%  新 {O.new.mean()*100:5.2f}%  "
          f"Δ{d.mean()*100:+5.2f}pt CI[{np.percentile(b,2.5):+.2f},{np.percentile(b,97.5):+.2f}]  "
          f"同一選択 {O.same_pick.mean()*100:4.1f}%  同ライン率 {O.cur_sl.mean()*100:.1f}%→{O.new_sl.mean()*100:.1f}%")

print("\n=== 軸2 の選び方: 現行(p3 2位) vs 条件付き同時確率 ===")
report(te[te["ne"]==9],"9車")
report(te[te["ne"]==7],"7車")
