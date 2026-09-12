#!/usr/bin/env python3
"""05-3 as-of 台: 2026-07-17〜09-10 の 7車レース（板の外・DB のみ）。
 入稿時点に実在した板（morning 07:0x / h12 / h14 / h18）と、結果・確定オッズ・型ラボの生成行を結合。
 出力 asof.pkl（dict of DataFrame）。DB は読み取りのみ。"""
import os, re, itertools, pickle
import numpy as np, pandas as pd, psycopg2
D = os.path.dirname(os.path.abspath(__file__))
CANON3 = list(itertools.combinations(range(1, 8), 3)); C3IDX = {frozenset(c): i for i, c in enumerate(CANON3)}
con = psycopg2.connect(os.environ["KEIRIN_DB_URL"]); cur = con.cursor(); cur.execute("SET statement_timeout=110000")
cur.execute("""SELECT race_key, race_date, venue_id, race_no, race_type, start_at::bigint, cup_grade, day_index, grade
               FROM keirin.wt_races WHERE race_date BETWEEN '2026-07-17' AND '2026-09-10' AND n_entries=7 AND cancel=0""")
races = pd.DataFrame(cur.fetchall(), columns="race_key race_date venue race_no race_type start_at cup_grade day_index grade".split())
keys = races.race_key.tolist(); print("races", len(keys))
cur.execute("""SELECT race_date, venue_id, MIN(start_at::bigint) FROM keirin.wt_races
               WHERE race_date BETWEEN '2026-07-17' AND '2026-09-10' AND cancel=0 GROUP BY 1,2""")
first = {(d, v): s for d, v, s in cur.fetchall()}
races["first_start"] = [first[(d, v)] for d, v in zip(races.race_date, races.venue)]
cur.execute("""SELECT race_key, frame_no, pred_top3_pct, pred_win_pct, prediction_mark, finish_order, line_group, line_pos,
               style, race_point FROM keirin.wt_entries WHERE race_key = ANY(%s)""", (keys,))
ent = pd.DataFrame(cur.fetchall(), columns="race_key frame_no p3 pw mark fin lg lpos style rp".split())
snap = []
for i0 in range(0, len(keys), 400):
    ch = keys[i0:i0+400]
    cur.execute("""SELECT race_key, snapshot_type, combination, odds_value, snapshot_at FROM keirin.wt_odds_snapshot
                   WHERE bet_type='trio' AND snapshot_type IN ('morning','h10','h12','h14','h18','h20') AND race_key = ANY(%s)""", (ch,))
    snap += cur.fetchall()
    print("  snap", i0, flush=True)
snap = pd.DataFrame(snap, columns="race_key stype comb odds at".split())
tf = []
for i0 in range(0, len(keys), 400):
    ch = keys[i0:i0+400]
    cur.execute("""SELECT race_key, snapshot_type, SUM(CASE WHEN odds_value>0 AND odds_value<9999 THEN 1 ELSE 0 END)
                   FROM keirin.wt_odds_snapshot WHERE bet_type='trifecta' AND race_key = ANY(%s) GROUP BY 1,2""", (ch,))
    tf += cur.fetchall()
tf = pd.DataFrame(tf, columns="race_key stype tf_fill".split())
fin = []
for i0 in range(0, len(keys), 400):
    ch = keys[i0:i0+400]
    cur.execute("SELECT race_key, combination, odds_value FROM keirin.wt_odds WHERE bet_type='trio' AND race_key = ANY(%s)", (ch,))
    fin += cur.fetchall()
fin = pd.DataFrame(fin, columns="race_key comb odds".split())
# 三連単の確定払戻（勝ち目だけ）
top = ent[ent.fin.isin([1, 2, 3])].sort_values(["race_key", "fin"]).groupby("race_key").frame_no.apply(list)
wincombo = {k: "-".join(str(int(x)) for x in v) for k, v in top.items() if len(v) == 3}
pairs = list(wincombo.items()); tfpay = {}
for i0 in range(0, len(pairs), 400):
    ch = pairs[i0:i0+400]
    cur.execute("""SELECT race_key, combination, odds_value FROM keirin.wt_odds WHERE bet_type='trifecta'
                   AND (race_key, combination) IN %s""", (tuple(ch),))
    for k, c, o in cur.fetchall():
        tfpay[k] = float(o) if o is not None else np.nan
cur.execute("""SELECT race_key, mode, plan_key, bet_type, n_legs, budget, type_label, axis_sum, arare, gap, pw_ent, axis1, axis2,
               p3_order, hit, payout, pred_mean_payout, pred_min_payout, settled_at, win_tf_odds, final_odds, legs, rule_version
               FROM keirin.type_lab_picks WHERE n_entries=7 AND mode IN ('paper','live') AND race_key = ANY(%s)""", (keys,))
picks = pd.DataFrame(cur.fetchall(), columns="race_key mode plan bet_type n_legs budget tl axis_sum arare gap pw_ent axis1 axis2 p3_order hit payout pred_mean pred_min settled_at win_tf_odds final_odds legs rule_version".split())
cur.execute("SELECT count(*) FROM keirin.wt_race_payouts"); print("wt_race_payouts rows", cur.fetchone()[0])
con.close()
races["win_combo"] = races.race_key.map(wincombo); races["tf_pay"] = races.race_key.map(tfpay)
with open(os.path.join(D, "asof.pkl"), "wb") as f:
    pickle.dump(dict(races=races, ent=ent, snap=snap, tf=tf, fin=fin, picks=picks), f)
print("saved; snap rows", len(snap), "fin rows", len(fin), "picks", len(picks), "tfpay", len(tfpay))
