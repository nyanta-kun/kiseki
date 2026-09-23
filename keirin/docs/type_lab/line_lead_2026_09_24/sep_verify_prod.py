import sys, pandas as pd, numpy as np
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))   # keirin/
from src.type_lab import race_shape, build_legs, PLANS, allocate
E=pd.read_csv("SEP/entries.csv"); R=pd.read_csv("SEP/races.csv")
S=pd.read_pickle("SEP/legs_sep.pkl"); S=S[S.lead_rk>=5]
# 検証側の買い目（レース→目の集合）: legs_sep は目ごとの行だが combo を持たないので作り直す
T=pd.read_csv("SEP/tf.csv"); 
exp={}
for rk,g in S.groupby("race_key"): exp[rk]=len(g)
got={}; alloc_ok=0
for rk,g in E[E.race_key.isin(set(R[R.n_entries==7].race_key))].groupby("race_key"):
    if len(g)!=7: continue
    cars=g.set_index("frame_no")
    p3={int(c):float(v)/100 for c,v in cars.pred_top3_pct.items()}
    sh=race_shape(p3,{int(c):v for c,v in cars.line_group.items()},{int(c):v for c,v in cars.line_pos.items()},
                  {int(c):v for c,v in cars["style"].items()},{int(c):float(v or 0) for c,v in cars.race_point.fillna(0).items()},
                  {int(c):0.0 for c in cars.index},2)
    if sh is None: continue
    po={t:30.0 for t in sh.lead_legs}
    legs=build_legs(sh,PLANS["L_lead"],po,{})
    if legs:
        got[rk]=len(legs); st=allocate(legs,po,{},PLANS["L_lead"])
        alloc_ok+= (len(set(st.values()))==1 and sum(st.values())<=10000)
both=set(exp)&set(got)
print(f"検証③のレース {len(exp)} / 本番関数 {len(got)} / 両方 {len(both)}")
print("点数が一致:",sum(exp[k]==got[k] for k in both),"/",len(both))
print("検証だけ:",sorted(set(exp)-set(got))[:10]," 本番だけ:",sorted(set(got)-set(exp))[:10])
print("均等配分で1万円以内:",alloc_ok,"/",len(got))
# --- 本番関数での9月の成績（1レース1万円均等・確定済みのみ）
OD={k:dict(zip(g.combination,g.odds_value)) for k,g in T.groupby("race_key")}
rows=[]
for rk,g in E[E.race_key.isin(set(R[R.n_entries==7].race_key))].groupby("race_key"):
    if len(g)!=7: continue
    fo=g.set_index("frame_no").finish_order; top=fo[(fo>=1)&(fo<=3)].sort_values()
    if len(top)!=3 or top.duplicated().any(): continue
    res="-".join(str(int(f)) for f in top.index)
    cars=g.set_index("frame_no")
    sh=race_shape({int(c):float(v)/100 for c,v in cars.pred_top3_pct.items()},{int(c):v for c,v in cars.line_group.items()},{int(c):v for c,v in cars.line_pos.items()},
                  {int(c):v for c,v in cars["style"].items()},{int(c):float(v or 0) for c,v in cars.race_point.fillna(0).items()},{int(c):0.0 for c in cars.index},2)
    if sh is None or not sh.lead_legs: continue
    od=OD.get(rk,{}); po={t:od.get("-".join(map(str,t)),0) for t in sh.lead_legs}; po={k:v for k,v in po.items() if v>0}
    legs=build_legs(sh,PLANS["L_lead"],po,{})
    if not legs: continue
    st=allocate(legs,po,{},PLANS["L_lead"]); inv=sum(st.values())
    hit=next((t for t in legs if "-".join(map(str,t))==res),None)
    rows.append(dict(rk=rk,day=rk[:8],inv=inv,ret=(od[res]*st[hit] if hit else 0),hit=hit is not None))
r=pd.DataFrame(rows); d=r.groupby("day")[["inv","ret"]].sum()
print(f"\n本番関数・9月: レース{len(r)} 投資{int(r.inv.sum()):,} 的中{r.hit.sum()} 払戻{int(r.ret.sum()):,} 回収率{r.ret.sum()/r.inv.sum()*100:.1f}% 最高{int(r.ret.max()):,} 100%超えの日{(d.ret>=d.inv).sum()}/{len(d)} 最大3本除く{(r.ret.sum()-r.ret.nlargest(3).sum())/r.inv.sum()*100:.1f}%")
