"""自信あり: 合成オッズの帯を変えたときの1日1件の成績（型ラボ・7車/9車）。"""
import sys, json, statistics as st, random, pickle
from pathlib import Path
from collections import defaultdict
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # keirin/
from src.database import get_connection
from src.confident_pick import (TYPE_LAB_CONFIDENT_PLANS, CONFIDENT_BEFORE_HOUR, start_hour_jst,
                                synthetic_odds, legs_hit_probability)
from src.type_lab import split_legs_by_role, sell_plans_for
OUT = sys.argv[1]
with get_connection() as c:
    rows = c.execute(
        "SELECT t.race_key, t.race_date, t.mode, t.plan_key, t.type_label, t.n_entries, t.race_type,"
        " t.axis_sum, t.pw_ent, t.legs, t.budget, t.hit, t.payout, t.void_refund, t.win_tf_odds, t.pred_mean_payout,"
        " r.start_at, t.final_odds, t.bet_type"
        " FROM type_lab_picks t JOIN wt_races r ON r.race_key = t.race_key"
        " WHERE t.settled_at IS NOT NULL AND t.mode IN ('paper','live','paper9','live9')"
        "   AND t.race_date >= '2024-07-01'").fetchall()
print("rows", len(rows), flush=True)
by_race = defaultdict(dict)
for r in rows:
    d = dict(zip(("race_key","race_date","mode","plan_key","type_label","n","race_type","axis_sum","pw_ent","legs",
                  "budget","hit","payout","void","win_tf_odds","pmp","start_at","final_odds","bet_type"), r))
    by_race[(d["race_key"], d["mode"].rstrip("9"))][d["plan_key"]] = d
out = []
for (rk, mode), plans in by_race.items():
    any_ = next(iter(plans.values()))
    # その日に売られる商品（1レース1商品）。A_trio はゲートを通れば A_trio
    trio = plans.get("A_trio")
    trio_ok = None
    if any_["type_label"] == "A" and trio:
        lg = json.loads(trio["legs"]) if isinstance(trio["legs"], str) else trio["legs"]
        trio_ok = bool(float(trio["pmp"] or 0) > 20000 and min(float(x["pred_odds"]) for x in lg) >= 2.0)
    sell = sell_plans_for(any_["type_label"], int(any_["n"]), any_["race_type"],
                          pw_ent=float(any_["pw_ent"] or 0) or None, trio_ok=trio_ok,
                          axis_sum=float(any_["axis_sum"] or 0))
    if not sell or sell[0].key not in plans: continue
    d = plans[sell[0].key]
    if d["plan_key"] not in TYPE_LAB_CONFIDENT_PLANS: continue
    h = start_hour_jst(d["start_at"])
    if h is None or h >= CONFIDENT_BEFORE_HOUR: continue
    legs = json.loads(d["legs"]) if isinstance(d["legs"], str) else d["legs"]
    base = split_legs_by_role(legs) or legs
    syn = synthetic_odds(base); p = legs_hit_probability(base)
    if syn is None or p is None: continue
    inv = sum(int(x["stake"]) for x in legs)
    ret = float(d["payout"] or 0) + float(d["void"] or 0)
    out.append(dict(date=str(d["race_date"]), mode=mode, rk=rk, plan=d["plan_key"], n=int(d["n"]), syn=syn, p=p,
                    inv=inv, ret=ret, hit=bool(d["hit"]), win_odds=float(d["win_tf_odds"]) if d["win_tf_odds"] else None,
                    bet=d["bet_type"]))
pickle.dump(out, open(OUT, "wb")); print("cands", len(out))
