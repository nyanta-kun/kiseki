"""2026-08 の実データで三連単 万車券枠を評価（vintage m2608・本番経路を使用）。"""
import sys, itertools, numpy as np, pandas as pd
sys.path.insert(0,'.'); sys.path.insert(0,'/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/7bc12223-92ab-4766-81d1-18bc01d1b207/scratchpad')
from scripts.build_7t1_candidates import _load_range, _predict, _meta_of
import src.odds_prediction_tf as odds_tf
from src.result_top3 import winning_trifectas
from src.database import get_connection
from tfprob import blend_pl

DFROM, DTO = "2026-08-01", "2026-08-23"
EVAL, WIN = "lgbm_wt_eval_m2608", "lgbm_wt_win_m2608"
print(f"期間 {DFROM}〜{DTO} / モデル {EVAL},{WIN}（学習は 2026-07-31 まで）")
by_race = _load_range(DFROM, DTO)
p3_all, pw_all = _predict(DFROM, DTO, EVAL, WIN)
print(f"7車レース {len(by_race)} / 予測できたレース {len(p3_all)}")

rks = list(by_race)
with get_connection() as c:
    fins, odds = {}, {}
    for i in range(0,len(rks),800):
        ch=rks[i:i+800]; ph=",".join("?"*len(ch))
        for r in c.execute(f"SELECT race_key, frame_no, finish_order FROM wt_entries "
                           f"WHERE finish_order>=1 AND race_key IN ({ph})", ch).fetchall():
            fins.setdefault(r["race_key"],[]).append((int(r["finish_order"]),int(r["frame_no"])))
        for r in c.execute(f"SELECT race_key, combination, odds_value FROM wt_odds "
                           f"WHERE bet_type='trifecta' AND race_key IN ({ph})", ch).fetchall():
            odds.setdefault(r["race_key"],{})[tuple(int(x) for x in r["combination"].split("-"))]=float(r["odds_value"])
print(f"結果あり {len(fins)} / オッズあり {len(odds)}")

rows=[]
for rk, ents in by_race.items():
    probs, pw = p3_all.get(rk), pw_all.get(rk)
    if not probs or len(probs)!=7 or not pw: continue
    grid = odds.get(rk); order = fins.get(rk)
    if not grid or not order: continue
    order_sorted=[fn for _,fn in sorted(order)]
    if len([o for o,_ in order if o<=3])<3: continue
    try:
        pred = odds_tf.predict_board(sorted(probs), probs, pw, _meta_of(ents))
    except Exception:
        continue
    cars=sorted(probs)
    P = blend_pl(cars, pw, probs, (1,.5,0))
    ws = set(winning_trifectas(sorted(order)))
    ent = -sum(p*np.log(p) for p in
               (np.array([pw[c] for c in cars])/sum(pw[c] for c in cars)) if p>0)
    band = {k:v for k,v in pred.items() if 100<=v<300}
    if not band: continue
    legs = sorted(band, key=lambda k: -(P.get(k,0)*band[k]))[:5]
    hitk = next((k for k in legs if k in ws), None)
    ao = grid.get(hitk) if hitk else None
    rows.append(dict(race_key=rk, date=ents[0]["race_date"], n=len(legs), p1_ent=ent,
        hit=int(hitk is not None), pay100=(ao*100 if ao else 0.0),
        combo="-".join(map(str,hitk)) if hitk else ""))
D=pd.DataFrame(rows); D["date"]=D.date.astype(str)
D.to_pickle("/tmp/aug_rows.pkl")
print(f"\n買えたレース {len(D)} / 日数 {D.date.nunique()}")

def rep(sub,label,unit=2000):
    R=len(sub); days=sub.date.nunique(); pts=sub.n.sum()
    inv100=pts*100; ret100=sub.pay100.sum()
    hits=int(sub.hit.sum()); w=sub[sub.pay100>=10000]
    print(f"\n■ {label}")
    print(f"  総レース数      : {R:,}R（{days}日 / 1日あたり平均 {R/days:.1f}R）")
    print(f"  買い目          : {pts:,}点（{pts/R:.1f}点/R）")
    print(f"  的中            : {hits}件（{hits/R*100:.2f}%）  うち万車券 {len(w)}件（{len(w)/R*100:.2f}%）")
    print(f"  回収率(ROI)     : {ret100/inv100*100:.1f}%")
    if hits:
        print(f"  払戻中央        : {int(sub[sub.hit==1].pay100.median()):,}円 / 100円換算")
        print(f"  最大払戻        : {int(sub.pay100.max()):,}円 / 100円換算"
              f"  → 1レース1万円({unit:,}円/点)なら {int(sub.pay100.max()*unit/100):,}円")
    print(f"  1レース1万円での収支: 投資 {R*unit*5//1000*1000:,}円 → 払戻 {int(ret100*unit/100):,}円"
          f" / 差引 {int(ret100*unit/100)-R*unit*5:+,}円（1日あたり {(int(ret100*unit/100)-R*unit*5)/days:+,.0f}円）")

rep(D,"A案: 全レース × EV順5点（推奨）")
thr = 1.5528   # 探索窓70%点（設計時に固定）
rep(D[D.p1_ent>=thr],"D案: 混戦上位30%(p1_ent>=1.5528) × EV順5点")
print("\n【的中の内訳（A案）】")
h=D[D.hit==1].sort_values("pay100",ascending=False)
print(h[["date","race_key","combo","pay100"]].head(12).to_string(index=False))
