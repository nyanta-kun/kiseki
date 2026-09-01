"""parsed.jsonl とDB(keirin)を突き合わせて分析用の1レース1行データを作る。"""
from __future__ import annotations
import json, os
from collections import defaultdict
from pathlib import Path
import psycopg2

ROOT = Path(__file__).resolve().parent / "atemaru"
DSN = os.environ["KEIRIN_DB_URL"]

rows = [json.loads(l) for l in (ROOT / "parsed.jsonl").open(encoding="utf-8")]
keys = sorted({f"{r['date']}_{r['venue_code']}_{r['race_no']:02d}" for r in rows})
print("races parsed:", len(rows), "keys:", len(keys))

conn = psycopg2.connect(DSN)
cur = conn.cursor()
cur.execute("""
select e.race_key, e.frame_no, e.name, e.style, e.race_point, e.line_group, e.line_pos,
       e.is_line_leader, e.n_lines, e.s_count, e.b_count, e.h_count,
       e.front_runner, e.stalker, e.deep_closer, e.marker,
       e.first_rate, e.second_rate, e.third_rate,
       e.pred_win_pct, e.pred_top3_pct, e.pred_top2_pct, e.finish_order, e.prediction_mark,
       r.race_type, r.grade, r.cup_grade, r.n_entries, r.day_index
from keirin.wt_entries e join keirin.wt_races r using(race_key)
where e.race_key = any(%s)
""", (keys,))
ent = defaultdict(dict)
meta = {}
for r in cur.fetchall():
    k, fn = r[0], r[1]
    ent[k][fn] = dict(zip(
        "name style race_point line_group line_pos is_line_leader n_lines s_count b_count h_count "
        "front_runner stalker deep_closer marker first_rate second_rate third_rate "
        "pred_win_pct pred_top3_pct pred_top2_pct finish_order prediction_mark".split(), r[2:24]))
    meta[k] = dict(zip("race_type grade cup_grade n_entries day_index".split(), r[24:]))

# 市場の1着確率（2車単から導出）
cur.execute("""select race_key, combination, odds_value from keirin.wt_odds
              where race_key = any(%s) and bet_type='exacta'""", (keys,))
mk = defaultdict(lambda: defaultdict(float))
for k, comb, od in cur.fetchall():
    if not od or od <= 0:
        continue
    a = int(comb.split("-")[0])
    mk[k][a] += 1.0 / float(od)

# 確定三連単オッズ（wt_race_payouts は 2026-07-04 で止まっているため代用）
cur.execute("""select race_key, combination, odds_value from keirin.wt_odds
              where race_key = any(%s) and bet_type='trifecta'""", (keys,))
tri = defaultdict(dict)
for k, comb, od in cur.fetchall():
    tri[k][comb] = float(od) if od else None

cur.execute("""select race_key, rank, pred_combo, n_combos, hit, payout, bet_amount
               from keirin.picks_history where race_key like any(%s)""",
            ([k + "%" for k in keys],))
picks = defaultdict(list)
for k, rank, combo, n, hit, pay, bet in cur.fetchall():
    picks[k.split("#")[0]].append({"rank": rank, "combo": combo, "n": n,
                                   "hit": hit, "payout": pay, "bet": bet})

out = []
for r in rows:
    k = f"{r['date']}_{r['venue_code']}_{r['race_no']:02d}"
    e = ent.get(k)
    if not e:
        continue
    m = mk.get(k, {})
    tot = sum(m.values()) or 1.0
    winp = {fn: m.get(fn, 0.0) / tot for fn in e}
    ranks = {fn: i + 1 for i, fn in enumerate(sorted(e, key=lambda f: -winp.get(f, 0)))}
    p3rank = {fn: i + 1 for i, fn in enumerate(sorted(e, key=lambda f: -float(e[f]["pred_top3_pct"])))}
    pwrank = {fn: i + 1 for i, fn in enumerate(sorted(e, key=lambda f: -float(e[f]["pred_win_pct"])))}
    ptrank = {fn: i + 1 for i, fn in enumerate(sorted(e, key=lambda f: -(e[f]["race_point"] or 0)))}
    out.append({**r, "race_key": k, "entries": e, "meta": meta[k],
                "tri_odds": tri.get(k, {}),
                "mkt_win_p": winp, "mkt_rank": ranks, "p3_rank": p3rank,
                "pw_rank": pwrank, "pt_rank": ptrank, "picks": picks.get(k, [])})

with (ROOT / "joined.jsonl").open("w", encoding="utf-8") as f:
    for o in out:
        f.write(json.dumps(o, ensure_ascii=False, default=float) + "\n")
print("joined:", len(out))
