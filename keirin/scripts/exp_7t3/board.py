"""全レースの予測三連単オッズ板を作る（本番 predict_board を使用）。"""
import pickle, sys, time, math, argparse
import numpy as np, pandas as pd
sys.path.insert(0,'.')
import src.odds_prediction_tf as tfo

ap=argparse.ArgumentParser(); ap.add_argument("--limit",type=int,default=0)
ap.add_argument("--out",default="/tmp/keirin_tf_board.pkl"); a=ap.parse_args()

d = pickle.load(open("/tmp/keirin_upset_ds.pkl","rb"))
E = d["E"].merge(d["pred"], on=["race_key","frame_no"], how="inner") \
          .merge(pickle.load(open("/tmp/keirin_e2.pkl","rb")), on=["race_key","frame_no"], how="left")
F0 = pickle.load(open("/tmp/keirin_upset_frame.pkl","rb"))
E = E[E.race_key.isin(set(F0.race_key))]
meta_json = tfo.load_meta()
booster = tfo.load_model(7)
keys = sorted(E.race_key.unique())
if a.limit: keys = keys[:a.limit]
E = E.set_index("race_key")
res={}; skipped=0; t0=time.time()
for i,rk in enumerate(keys):
    g = E.loc[[rk]]
    if len(g)!=7: skipped+=1; continue
    cars = g.frame_no.astype(int).tolist()
    p3 = dict(zip(cars, g.pp3.astype(float)))
    pw = dict(zip(cars, g.ppw.astype(float)))
    meta = {int(r.frame_no): dict(race_point=r.race_point, line_group=r.line_group,
            line_size=r.line_size, line_pos=r.line_pos, is_line_leader=r.is_line_leader,
            mark=r.prediction_mark, player_class=r.player_class, style=r.style,
            first_rate=r.first_rate, second_rate=r.second_rate, third_rate=r.third_rate)
            for r in g.itertuples()}
    try:
        combos, X = tfo.build_race_features(cars, p3, pw, meta)
        raw = np.clip(np.power(10.0, booster.predict(X)), 1.0, None)
        raw *= float((1.0/raw).sum())/tfo.target_sum(7)
        res[rk] = (combos, raw.astype(np.float32))
    except Exception as e:
        skipped+=1
        if skipped<=3: print("ERR", rk, type(e).__name__, e)
    if (i+1)%2000==0:
        print(f"  {i+1}/{len(keys)}  {time.time()-t0:.0f}s", flush=True)
print(f"done {len(res):,} races / skipped {skipped} / {time.time()-t0:.0f}s")
pickle.dump(res, open(a.out,"wb"))
