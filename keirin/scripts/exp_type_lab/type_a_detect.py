#!/usr/bin/env python3
"""型A の波乱を**事前に**当てられるか（2025学習 → 2026評価・2026-08-31）。

🔴 目的を2つに分けて測るのが肝。
   U30 = 確定三連単 30倍以上で決着（＝荒れたか）
   T   = {軸1,軸2} が3着内 ∧ 30倍以上（＝**軸商品で取りうる荒れ**）
   100倍+ の4割は軸1が3着にも入らないので、U30 を当てても買いにならない。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/type_a_detect.py
"""
import importlib.util, sys, math
from collections import defaultdict
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(REPO))
from src.database import get_connection
from src.marquee import is_fill_target
_s = importlib.util.spec_from_file_location("g", REPO.parent/"backend/src/services/keirin_type_lab_gate.py")
G = importlib.util.module_from_spec(_s); _s.loader.exec_module(G)

with get_connection() as c:
    rows = [dict(r) for r in c.execute(
        "SELECT race_key, race_date, race_type, venue_name, day_index, axis_sum, arare, gap, "
        "       p3_order, win_combo, win_tf_odds FROM type_lab_picks "
        "WHERE mode='paper' AND plan_key='A_hit' AND settled_at IS NOT NULL AND n_entries=7 "
        "  AND win_tf_odds IS NOT NULL AND win_combo IS NOT NULL")]
    rows = [d for d in rows if is_fill_target(d.get("race_type"), None) or
            G.passes_axis_gate("A_hit", float(d["axis_sum"]) if d["axis_sum"] is not None else None, 7)]
    keys = sorted({d["race_key"] for d in rows})
    ent = defaultdict(dict)
    for i in range(0, len(keys), 300):
        ch = keys[i:i+300]; ph = ",".join("?"*len(ch))
        for r in c.execute(f"SELECT race_key, frame_no, pred_win_pct, pred_top3_pct, "
                           f" line_size, n_lines, is_line_leader, style, race_point "
                           f"FROM wt_entries WHERE race_key IN ({ph})", tuple(ch)):
            d = dict(r); ent[d["race_key"]][int(d["frame_no"])] = d

def ent_h(ps):
    s = sum(ps) or 1.0
    return -sum((p/s)*math.log((p/s)+1e-12) for p in ps)

data = []
for d in rows:
    e = ent.get(d["race_key"], {})
    if len(e) != 7 or any(x["pred_win_pct"] is None or x["pred_top3_pct"] is None for x in e.values()):
        continue
    o = [int(x) for x in str(d["p3_order"]).replace(",", "-").split("-") if x]
    f = [int(x) for x in str(d["win_combo"]).split("-")]
    pw = sorted((float(e[c]["pred_win_pct"]) for c in e), reverse=True)
    p3 = sorted((float(e[c]["pred_top3_pct"]) for c in e), reverse=True)
    od = float(d["win_tf_odds"])
    data.append(dict(
        date=str(d["race_date"]), odds=od,
        T=int(o[0] in f and o[1] in f and od >= 30),
        U30=int(od >= 30), U100=int(od >= 100),
        axis_sum=float(d["axis_sum"] or 0), arare=int(d["arare"] or 0),
        gap=float(d["gap"] or 0), rtype=str(d["race_type"] or ""),
        pw_max=pw[0], pw_ent=ent_h(pw), p3_ent=ent_h(p3),
        pw_gap12=pw[0]-pw[1], p3_gap23=p3[1]-p3[2], p3_gap34=p3[2]-p3[3],
        nlines=float(e[1]["n_lines"] or 0),
        rp_sd=(lambda v: (sum((x-sum(v)/7)**2 for x in v)/7)**.5)(
            [float(e[c]["race_point"] or 0) for c in e]),
    ))

tr = [d for d in data if d["date"] <= "2025-12-31"]
te = [d for d in data if d["date"] >= "2026-01-01"]
print(f"学習 {len(tr):,}R  評価 {len(te):,}R")
for tgt in ("T", "U30", "U100"):
    print(f"  基準率  学習 {sum(d[tgt] for d in tr)/len(tr):.1%} / 評価 {sum(d[tgt] for d in te)/len(te):.1%}   ({tgt})")

def auc(y, s):
    pair = sorted(zip(s, y))
    n1 = sum(y); n0 = len(y)-n1
    if not n1 or not n0: return .5
    r = {}; i = 0
    while i < len(pair):
        j = i
        while j+1 < len(pair) and pair[j+1][0] == pair[i][0]: j += 1
        rk = (i+j)/2 + 1
        for k in range(i, j+1): r[k] = rk
        i = j+1
    s1 = sum(r[k] for k in range(len(pair)) if pair[k][1] == 1)
    return (s1 - n1*(n1+1)/2) / (n1*n0)

FEATS = ["axis_sum","arare","gap","pw_max","pw_ent","p3_ent","pw_gap12",
         "p3_gap23","p3_gap34","nlines","rp_sd"]
for tgt in ("T","U30"):
    print(f"\n=== 目的 {tgt} ===")
    print("  単一量の AUC（評価窓 2026・符号は学習窓で決める）")
    best = None
    for f in FEATS:
        a_tr = auc([d[tgt] for d in tr], [d[f] for d in tr])
        sgn = 1 if a_tr >= .5 else -1
        a = auc([d[tgt] for d in te], [sgn*d[f] for d in te])
        print(f"    {f:<12} 学習 {max(a_tr,1-a_tr):.3f}   評価 {a:.3f}  (符号 {'+' if sgn>0 else '-'})")
        if best is None or a > best[1]: best = (f, a)
    # LightGBM
    try:
        import lightgbm as lgb, numpy as np
        Xtr = np.array([[d[f] for f in FEATS] for d in tr]); ytr = np.array([d[tgt] for d in tr])
        Xte = np.array([[d[f] for f in FEATS] for d in te]); yte = np.array([d[tgt] for d in te])
        m = lgb.train(dict(objective="binary", learning_rate=.05, num_leaves=15,
                           min_data_in_leaf=100, feature_fraction=.8, bagging_fraction=.8,
                           bagging_freq=1, verbose=-1, seed=0),
                      lgb.Dataset(Xtr, ytr), num_boost_round=250)
        p = m.predict(Xte)
        print(f"  LightGBM({len(FEATS)}特徴) 評価 AUC {auc(list(yte), list(p)):.3f}"
              f"   ↔ 単一最良 {best[0]} {best[1]:.3f}")
        srt = sorted(zip(p, yte), key=lambda t: -t[0])
        base = yte.mean()
        for q in (.1,.2,.3,.5):
            k = int(len(srt)*q)
            print(f"    上位{q:.0%}  該当率 {sum(y for _,y in srt[:k])/k:.1%}  リフト {sum(y for _,y in srt[:k])/k/base:.2f}倍")
    except ImportError:
        print("  (lightgbm 無し)")
