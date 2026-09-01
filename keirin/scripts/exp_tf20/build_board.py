#!/usr/bin/env python3
"""三連単「1日20レース枠」検証用の板を作る（2026-08-25）。

vintage walk-forward 予測（`data/exp_cache/wf_preds_*.pkl`）と本番の
`odds_prediction_tf.predict_board` から、7車全レースの
**210点の買い目確率と予測オッズ**を作って npz に落とす。

出力: /tmp/tf20_board.npz
  PROB (N,210) 位置別合成PL の買い目確率 / PO (N,210) 予測オッズ
  WIN  (N)     的中する買い目の index（-1=不明）/ PAY (N) 実払戻(円/100円)
  DATE DAYIDX RTYPE GRADE VENUE KEY

⚠️ 予測オッズモデル `odds_tf_n7` の train_end は 2025-12-31。
   **2024-25 窓は in-sample**。確認は 2026 窓で行うこと。
"""
from __future__ import annotations

import glob
import itertools
import json
import os
import pickle
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import psycopg2

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.chdir(REPO)

import src.odds_prediction_tf as tfo  # noqa: E402
from scripts.exp_7t3.tfprob import blend_pl  # noqa: E402

CANON = list(itertools.permutations(range(1, 8), 3))
CIDX = {c: i for i, c in enumerate(CANON)}

t0 = time.time()
# ── vintage 予測 ──
pred = {}
for f in sorted(glob.glob("data/exp_cache/wf_preds_2*.pkl")):
    df = pickle.load(open(f, "rb"))
    for rk, fn, p3, pw in zip(df.race_key, df.frame_no, df.pp3, df.ppw):
        pred.setdefault(rk, {})[int(fn)] = (float(p3), float(pw))
print(f"vintage予測 {len(pred):,}R  {time.time()-t0:.0f}s", flush=True)

# ── 実払戻（7h1 キャッシュ = 的中買い目の払戻/100円）──
pay = {}
for line in open("data/exp/7h1_gate_cache.jsonl"):
    r = json.loads(line)
    if r.get("trifecta_payout"):
        pay[r["race_key"]] = float(r["trifecta_payout"])
print(f"払戻 {len(pay):,}R", flush=True)

con = psycopg2.connect(os.environ["KEIRIN_DB_URL"])
cur = con.cursor()
cur.execute("""
 select r.race_key, r.race_date::text, coalesce(r.day_index,0),
        coalesce(r.race_type,''), coalesce(r.grade,''), coalesce(r.cup_grade::text,''),
        substring(r.race_key,10,2)
 from keirin.wt_races r
 where r.n_entries = 7 and r.race_date between '2024-07-01' and '2026-08-25'
 order by r.race_key""")
races = cur.fetchall()
keys = [r[0] for r in races]
print(f"7車レース {len(races):,}", flush=True)

ent = defaultdict(dict)
fin = defaultdict(dict)
for i in range(0, len(keys), 3000):
    cur.execute("""
     select race_key, frame_no, race_point, line_group, line_size, line_pos,
            is_line_leader, prediction_mark, player_class, style,
            first_rate, second_rate, third_rate, finish_order
     from keirin.wt_entries where race_key = any(%s)""", (keys[i:i + 3000],))
    for (rk, fn, rp, lg, lsz, lp, ld, mk, cls_, sty, f1, f2, f3, fo) in cur.fetchall():
        ent[rk][int(fn)] = dict(race_point=rp, line_group=lg, line_size=lsz,
                                line_pos=lp, is_line_leader=ld, mark=mk,
                                player_class=cls_, style=sty,
                                first_rate=f1, second_rate=f2, third_rate=f3)
        if fo is not None:
            fin[rk][int(fo)] = int(fn)
con.close()
print(f"出走表 {len(ent):,}R  {time.time()-t0:.0f}s", flush=True)

tfo.load_model(7)
meta_json = tfo.load_meta()
PROB, PO, WIN, PAY = [], [], [], []
KEY, DATE, DAYIDX, RTYPE, GRADE, CUPG, VENUE = [], [], [], [], [], [], []
skip = defaultdict(int)
for n, (rk, d, di, rt, gr, cg, vn) in enumerate(races):
    if n % 5000 == 0:
        print(f"  {n:,}/{len(races):,}  {time.time()-t0:.0f}s", flush=True)
    p = pred.get(rk); e = ent.get(rk); f = fin.get(rk)
    if not p or not e or rk not in pay:
        skip["no_pred_or_pay"] += 1; continue
    cars = sorted(e)
    if len(cars) != 7 or any(c not in p for c in cars):
        skip["cars"] += 1; continue
    p3 = {c: p[c][0] for c in cars}
    pw = {c: p[c][1] for c in cars}
    try:
        board = tfo.predict_board(cars, p3, pw, e, meta_json) \
            if tfo.predict_board.__code__.co_argcount == 5 \
            else tfo.predict_board(cars, p3, pw, e)
    except Exception:
        skip["board"] += 1; continue
    pr = blend_pl(cars, pw, p3)
    if len(board) != 210 or len(pr) != 210:
        skip["len"] += 1; continue
    PROB.append([pr[c] for c in CANON])
    PO.append([board[c] for c in CANON])
    w = -1
    if f and all(k in f for k in (1, 2, 3)):
        t = (f[1], f[2], f[3])
        w = CIDX.get(t, -1)
    WIN.append(w); PAY.append(pay[rk])
    KEY.append(rk); DATE.append(d); DAYIDX.append(di)
    RTYPE.append(rt); GRADE.append(gr); CUPG.append(cg); VENUE.append(vn)

print(f"skip: {dict(skip)}")
np.savez_compressed("/tmp/tf20_board.npz",
                    PROB=np.array(PROB, np.float32), PO=np.array(PO, np.float32),
                    WIN=np.array(WIN, np.int32), PAY=np.array(PAY, np.float64),
                    KEY=np.array(KEY), DATE=np.array(DATE),
                    DAYIDX=np.array(DAYIDX, np.int16), RTYPE=np.array(RTYPE),
                    GRADE=np.array(GRADE), CUPG=np.array(CUPG), VENUE=np.array(VENUE))
print(f"保存 {len(PROB):,}R  {time.time()-t0:.0f}s")
