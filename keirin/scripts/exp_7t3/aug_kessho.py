"""2026-08 ライブ検証: 決勝のみ × 30倍以上 × 5点。"""
import sys, numpy as np, pandas as pd
sys.path.insert(0,'.'); sys.path.insert(0,'/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/7bc12223-92ab-4766-81d1-18bc01d1b207/scratchpad')
from scripts.build_7t1_candidates import _load_range, _predict, _meta_of
import src.odds_prediction_tf as odds_tf
from src.result_top3 import winning_trifectas
from src.database import get_connection
from tfprob import blend_pl
DFROM,DTO="2026-08-01","2026-08-23"
by_race=_load_range(DFROM,DTO); p3a,pwa=_predict(DFROM,DTO,"lgbm_wt_eval_m2608","lgbm_wt_win_m2608")
rks=list(by_race)
with get_connection() as c:
    fins,odds={},{}
    for i in range(0,len(rks),800):
        ch=rks[i:i+800]; ph=",".join("?"*len(ch))
        for r in c.execute(f"SELECT race_key,frame_no,finish_order FROM wt_entries WHERE finish_order>=1 AND race_key IN ({ph})",ch).fetchall():
            fins.setdefault(r["race_key"],[]).append((int(r["finish_order"]),int(r["frame_no"])))
        for r in c.execute(f"SELECT race_key,combination,odds_value FROM wt_odds WHERE bet_type='trifecta' AND race_key IN ({ph})",ch).fetchall():
            odds.setdefault(r["race_key"],{})[tuple(int(x) for x in r["combination"].split("-"))]=float(r["odds_value"])
rows=[]
for rk,ents in by_race.items():
    probs,pw=p3a.get(rk),pwa.get(rk)
    grid,order=odds.get(rk),fins.get(rk)
    if not probs or len(probs)!=7 or not pw or not grid or not order: continue
    if len([o for o,_ in order if o<=3])<3: continue
    try: pred=odds_tf.predict_board(sorted(probs),probs,pw,_meta_of(ents))
    except Exception: continue
    cars=sorted(probs); P=blend_pl(cars,pw,probs,(1,.5,0))
    ws=set(winning_trifectas(sorted(order)))
    band={k:v for k,v in pred.items() if v>=30}
    if not band: continue
    legs=sorted(band,key=lambda k:-P.get(k,0))[:5]
    hk=next((k for k in legs if k in ws),None)
    rows.append(dict(rk=rk,date=str(ents[0]["race_date"]),rtype=ents[0]["race_type"],
        n=len(legs),hit=int(hk is not None),pay=(grid.get(hk,0)*100 if hk else 0.0),
        combo="-".join(map(str,hk)) if hk else ""))
D=pd.DataFrame(rows); D.to_pickle("/tmp/aug_k.pkl")
def rep(s,label,unit):
    R=len(s); days=s.date.nunique(); pts=s.n.sum(); h=int(s.hit.sum())
    print(f"\n■ {label}")
    print(f"  総レース {R}R / {days}日 / 1日平均 {R/days:.1f}R / {pts/R:.1f}点")
    print(f"  的中 {h}件（{h/R*100:.2f}%）→ 週 {h/days*7:.2f}ヒット")
    print(f"  回収率 {s.pay.sum()/(pts*100)*100:.1f}%")
    if h: print(f"  払戻中央 {int(s[s.hit==1].pay.median()):,}円 / 最大 {int(s.pay.max()):,}円（100円あたり）"
                f" → 1レース1万円({unit:,}円/点)なら中央 {int(s[s.hit==1].pay.median()*unit/100):,}円 / 最大 {int(s.pay.max()*unit/100):,}円")
K=D[D.rtype.isin(["決勝","チャレンジ決勝"])]
rep(K,"決勝のみ × 30倍以上 × 5点",2000)
rep(D[D.rtype.isin(["決勝","チャレンジ決勝","ガールズ決勝"])],"決勝+ガールズ決勝 × 30倍以上 × 5点",2000)
rep(D,"全レース × 30倍以上 × 5点（対照）",2000)
print("\n【決勝のみの的中一覧】")
print(K[K.hit==1][["date","rk","rtype","combo","pay"]].sort_values("pay",ascending=False).to_string(index=False))
