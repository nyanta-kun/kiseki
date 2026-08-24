"""2026-04〜08 を月次 vintage で検証（本番と同じ経路）。"""
import sys, numpy as np, pandas as pd
sys.path.insert(0,'.'); sys.path.insert(0,'/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/7bc12223-92ab-4766-81d1-18bc01d1b207/scratchpad')
from scripts.build_7t1_candidates import _load_range, _predict, _meta_of
import src.odds_prediction_tf as odds_tf
from src.result_top3 import winning_trifectas
from src.database import get_connection
from src.strategy_wt import rank_7t1_select, rank_7t1_stakes, rank_7t1_is_target_race_type, rank_7t1_pl_prob
from tfprob import blend_pl

MONTHS=[("2026-04-01","2026-04-30","m2604"),("2026-05-01","2026-05-31","m2605"),
        ("2026-06-01","2026-06-30","m2606"),("2026-07-01","2026-07-31","m2607"),
        ("2026-08-01","2026-08-23","m2608")]
allrows=[]
for a,b,tag in MONTHS:
    by=_load_range(a,b); p3a,pwa=_predict(a,b,f"lgbm_wt_eval_{tag}",f"lgbm_wt_win_{tag}")
    rks=list(by)
    with get_connection() as c:
        fins,odds={},{}
        for i in range(0,len(rks),800):
            ch=rks[i:i+800]; ph=",".join("?"*len(ch))
            for r in c.execute(f"SELECT race_key,frame_no,finish_order FROM wt_entries WHERE finish_order>=1 AND race_key IN ({ph})",ch).fetchall():
                fins.setdefault(r["race_key"],[]).append((int(r["finish_order"]),int(r["frame_no"])))
            for r in c.execute(f"SELECT race_key,combination,odds_value FROM wt_odds WHERE bet_type='trifecta' AND race_key IN ({ph})",ch).fetchall():
                odds.setdefault(r["race_key"],{})[tuple(int(x) for x in r["combination"].split("-"))]=float(r["odds_value"])
    n=0
    for rk,ents in by.items():
        p3,pw=p3a.get(rk),pwa.get(rk); grid,order=odds.get(rk),fins.get(rk)
        if not p3 or len(p3)!=7 or not pw or not grid or not order: continue
        if len([o for o,_ in order if o<=3])<3: continue
        try: po=odds_tf.predict_board(sorted(p3),p3,pw,_meta_of(ents))
        except Exception: continue
        cars=sorted(p3); rt=str(ents[0]["race_type"])
        ws=set(winning_trifectas(sorted(order)))
        P=blend_pl(cars,pw,p3,(1,.5,0))
        band={k:v for k,v in po.items() if v>=30}
        legs=sorted(band,key=lambda k:-P.get(k,0))[:5] if band else []
        hk=next((k for k in legs if k in ws),None)
        # 7T1
        o3=sorted(cars,key=lambda c:-p3[c]); lg={int(e["frame_no"]):e.get("line_group") for e in ents}
        cross = not (lg.get(o3[0]) is not None and lg.get(o3[0])==lg.get(o3[1]))
        sel=rank_7t1_select(p3,pw,po)
        t1=[]; ev=None
        if sel and rank_7t1_is_target_race_type(rt) and cross:
            t1=sel[2]; st=rank_7t1_stakes(t1); tot=sum(st.values())
            ev=sum((rank_7t1_pl_prob(pw,l) or 0)*po.get(tuple(int(x) for x in l.split("-")),0)*st[l] for l in t1)/tot if tot else None
        t1h=next((l for l in t1 if tuple(int(x) for x in l.split("-")) in ws),None)
        allrows.append(dict(月=a[:7],date=str(ents[0]["race_date"]),rk=rk,rtype=rt,
            new=rt in ("決勝","チャレンジ決勝") and bool(legs), new_n=len(legs),
            new_hit=int(hk is not None), new_pay=(grid.get(hk,0) if hk else 0.0),
            t1=bool(t1), t1_legs=t1, t1_ev=ev, t1_hit=t1h,
            t1_pay=(grid.get(tuple(int(x) for x in t1h.split("-")),0) if t1h else 0.0)))
        n+=1
    print(f"{a[:7]}: {n}R 処理", flush=True)
D=pd.DataFrame(allrows); D.to_pickle("/tmp/months.pkl"); print("saved", len(D))
