#!/usr/bin/env python3
"""H16 Step0 持続性用: 2026-06〜 の朝スナップショット（三連複・三連単）と最終（wt_odds 最新）を抽出（読み取り専用）。

対象 = type_lab_picks の hit 三連単プラン（A/B/C/E/F_hit）の 7車行。買い目の組は legs から。
出力: data/exp_bet_review/h16_snap_2026.pkl  {picks:[(race_key,plan,date,[combo..],[stake..])], odds:{race_key:{m3,mtf,f3,ftf}}}
"""
from __future__ import annotations
import pickle, re, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
from _db import connect

SENT = 9999.0


def parse(comb):
    try:
        t = tuple(int(x) for x in re.split(r"[-=→]", str(comb)))
    except ValueError:
        return None
    return t if len(t) == 3 else None


def main():
    with connect() as c, c.cursor() as cur:
        cur.execute("""SELECT DISTINCT ON (race_key, plan_key) race_key, plan_key, race_date::text, legs
                       FROM keirin.type_lab_picks
                       WHERE plan_key IN ('A_hit','B_hit','C_hit','E_hit','F_hit') AND n_entries=7
                         AND race_date >= '2026-06-08' AND bet_type='trifecta'
                       ORDER BY race_key, plan_key, generated_at DESC""")
        picks = []
        for rk, pk, d, legs in cur.fetchall():
            cs = [parse(l["combo"]) for l in legs]
            if any(x is None for x in cs):
                continue
            picks.append((rk, pk, d, cs, [int(l["stake"]) for l in legs]))
        keys = sorted({p[0] for p in picks})
        print("picks", len(picks), "races", len(keys), flush=True)
        odds = {k: dict(m3={}, mtf={}, f3={}, ftf={}) for k in keys}
        for i0 in range(0, len(keys), 200):
            ch = keys[i0:i0 + 200]
            for bt, nm_m, nm_f in (("trio", "m3", "f3"), ("trifecta", "mtf", "ftf")):
                cur.execute("""SELECT DISTINCT ON (race_key, combination) race_key, combination, odds_value
                               FROM keirin.wt_odds_snapshot WHERE bet_type=%s AND snapshot_type='morning'
                               AND race_key = ANY(%s) ORDER BY race_key, combination, snapshot_at DESC""", (bt, ch))
                for rk, comb, v in cur.fetchall():
                    t = parse(comb) if bt == "trifecta" else None
                    if bt == "trio":
                        try:
                            t = frozenset(int(x) for x in re.split(r"[-=→]", str(comb)))
                        except ValueError:
                            continue
                        if len(t) != 3:
                            continue
                    if t is None or v is None or not (0 < float(v) < SENT):
                        continue
                    odds[rk][nm_m][t] = float(v)
                cur.execute("""SELECT DISTINCT ON (race_key, combination) race_key, combination, odds_value
                               FROM keirin.wt_odds WHERE bet_type=%s AND race_key = ANY(%s)
                               ORDER BY race_key, combination, collected_at DESC""", (bt, ch))
                for rk, comb, v in cur.fetchall():
                    t = parse(comb) if bt == "trifecta" else None
                    if bt == "trio":
                        try:
                            t = frozenset(int(x) for x in re.split(r"[-=→]", str(comb)))
                        except ValueError:
                            continue
                        if len(t) != 3:
                            continue
                    if t is None or v is None or not (0 < float(v) < SENT):
                        continue
                    odds[rk][nm_f][t] = float(v)
            print(min(i0 + 200, len(keys)), len(keys), flush=True)
    pickle.dump(dict(picks=picks, odds=odds), open(REPO / "data/exp_bet_review/h16_snap_2026.pkl", "wb"))


if __name__ == "__main__":
    main()
