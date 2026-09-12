"""7M1 の「市場乖離（モデル上位2車 ≠ {WT◎,WT○}）」を型ラボの売り物へ当てる。"""
import os, sys, psycopg2, numpy as np
from collections import defaultdict
sys.path.insert(0, '/Users/ysuzuki/GitHub/kiseki/keirin')
from src.type_lab import sell_plans_for
import importlib.util
spec = importlib.util.spec_from_file_location(
    "gate", "/Users/ysuzuki/GitHub/kiseki/backend/src/services/keirin_type_lab_gate.py")
gate = importlib.util.module_from_spec(spec); spec.loader.exec_module(gate)

con = psycopg2.connect(os.environ["KEIRIN_DB_URL"]); cur = con.cursor()
cur.execute("""SELECT race_key, frame_no, pred_top3_pct, prediction_mark
               FROM keirin.wt_entries e
               WHERE race_key IN (SELECT DISTINCT race_key FROM keirin.type_lab_picks
                                  WHERE mode='paper' AND n_entries=7)""")
ent = defaultdict(list)
for rk, fn, p3, mk in cur.fetchall():
    ent[rk].append((int(fn), float(p3 or 0), mk))
agree = {}
for rk, es in ent.items():
    top2 = {f for f, _, _ in sorted(es, key=lambda x: -x[1])[:2]}
    marks = {f for f, _, mk in es if mk in (1, 2)}
    agree[rk] = (len(marks) == 2 and top2 == marks)

cur.execute("""SELECT race_key,race_date,type_label,plan_key,budget,
  pred_mean_payout,payout,legs,race_type,axis_sum,pw_ent
 FROM keirin.type_lab_picks WHERE mode='paper' AND settled_at IS NOT NULL AND n_entries=7""")
by_race = defaultdict(dict); info = {}
for rk, d, tl, pk, bud, pmp, pay, legs, rt, axs, pwe in cur.fetchall():
    by_race[rk][pk] = dict(bud=bud, pmp=float(pmp or 0), legs=legs, pay=pay or 0, axs=float(axs or 0))
    info[rk] = dict(d=str(d), tl=tl, rt=rt, pwe=float(pwe) if pwe is not None else None)

def gated(r):
    if r["pmp"] <= 20000: return False
    po = [l.get("pred_odds") for l in r["legs"] if l.get("pred_odds")]
    return bool(po) and min(po) >= 2.0

agg = defaultdict(lambda: dict(n=0, shown=0, inv=0, pay=0, hp=0, pays=[]))
days = defaultdict(set)
for rk, plans in by_race.items():
    i = info[rk]
    trio_ok = "A_trio" in plans and gated(plans["A_trio"])
    sp = sell_plans_for(i["tl"], 7, i["rt"], pw_ent=i["pwe"], trio_ok=trio_ok)
    if not sp: continue
    pk = sp[0].key; r = plans.get(pk)
    if r is None or not gated(r) or not gate.passes_axis_gate(pk, r["axs"]): continue
    w = "2025" if i["d"] < "2026-01-01" else "2026"
    g = "一致" if agree.get(rk) else "乖離"
    a = agg[(w, g)]
    a["n"] += 1; a["inv"] += r["bud"]; a["pay"] += r["pay"]
    a["shown"] += 1 if r["pay"] > r["bud"] else 0
    a["hp"] += 1 if r["pay"] >= 100000 else 0
    if r["pay"]: a["pays"].append(r["pay"])
    days[w].add(i["d"])
for (w, g), a in sorted(agg.items()):
    nd = len(days[w])
    print(f"{w} {g} n={a['n']:6d} 件/日={a['n']/nd:5.2f} 表示的中={a['shown']/a['n']*100:5.2f}% "
          f"ROI={a['pay']/a['inv']*100:5.1f}% 中央={int(np.median(a['pays'])) if a['pays'] else 0:7d} 10万+/日={a['hp']/nd:.3f}")
