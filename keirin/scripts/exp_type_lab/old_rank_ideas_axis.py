#!/usr/bin/env python3
"""7S の3ヘッド軸を型ラボの軸選定に当てる（本番の重み 0.3 と 7S 設計値 0.5 の両方）。"""
from __future__ import annotations
import pickle, sys
from collections import defaultdict
import numpy as np, pandas as pd
sys.path.insert(0, "/Users/ysuzuki/GitHub/kiseki/keirin")
from src.database import get_connection
from src.preprocessing.feature_wt import load_features_wt, prepare_X
from src.strategy_wt import rank_7s_select_axis, RANK_AXIS2_BAD_WEIGHT

MODEL_DIR = "/Users/ysuzuki/GitHub/kiseki/keirin/data/models"
def _raw(n):
    return pickle.load(open(f"{MODEL_DIR}/{n}.pkl", "rb"))
def _cols(m):
    c = getattr(m, "feature_name_", None)
    return list(c) if c else None
def _proba(m, X):
    return m.predict_proba(X)[:, 1] if hasattr(m, "predict_proba") else m.predict(X)

with get_connection() as c:
    fin = defaultdict(dict)
    for r in c.execute("""SELECT e.race_key, e.frame_no, e.finish_order, r.race_date
                          FROM wt_entries e JOIN wt_races r ON r.race_key=e.race_key
                          WHERE r.n_entries=7 AND r.cancel=0
                            AND r.race_date BETWEEN '2024-07-01' AND '2026-08-31'"""):
        fin[r["race_key"]][int(r["frame_no"])] = (r["finish_order"], str(r["race_date"]))

feat = load_features_wt("2022-12-01", "2026-08-31", use_cache=True)
feat = feat[feat["race_key"].isin(set(fin))].copy()
feat["_m"] = pd.to_datetime(feat["race_date"]).dt.strftime("%y%m")
P = defaultdict(dict)
for m in sorted(feat["_m"].unique()):
    sub = feat[feat["_m"] == m]
    try:
        ev, wi, ba = _raw(f"lgbm_wt_eval_m{m}"), _raw(f"lgbm_wt_win_m{m}"), _raw(f"lgbm_wt_bad_m{m}")
    except FileNotFoundError:
        continue
    X = prepare_X(sub)
    o = [_proba(mm, X[_cols(mm)] if _cols(mm) else X) for mm in (ev, wi, ba)]
    for rk, fn, a, b, cc in zip(sub["race_key"], sub["frame_no"], *o):
        P[rk][int(fn)] = (float(a), float(b), float(cc))

res = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))   # [n, both, swapped_both]
for rk, pr in P.items():
    fo = fin.get(rk)
    if not fo or len(pr) != 7:
        continue
    top3 = {f for f, (o, _) in fo.items() if o in (1, 2, 3)}
    if len(top3) != 3:
        continue
    d = next(iter(fo.values()))[1]
    w = "探索" if d < "2026-01-01" else "確認"
    p3 = {f: pr[f][0] for f in pr}; pw = {f: pr[f][1] for f in pr}; bd = {f: pr[f][2] for f in pr}
    order = sorted(p3, key=lambda f: -p3[f])
    cur = (order[0], order[1])
    for wt in (0.3, 0.5):
        r = rank_7s_select_axis(pw, p3, bd, bad_weight=wt)
        if r is None:
            continue
        a1, a2, _ = r
        s = res[w][wt]
        s[0] += 1
        s[1] += 1 if (cur[0] in top3 and cur[1] in top3) else 0
        s[2] += 1 if (a1 in top3 and a2 in top3) else 0
        if {a1, a2} != set(cur):
            t = res[w][f"swap{wt}"]
            t[0] += 1
            t[1] += 1 if (cur[0] in top3 and cur[1] in top3) else 0
            t[2] += 1 if (a1 in top3 and a2 in top3) else 0

for w in ("探索", "確認"):
    for k, s in res[w].items():
        if not s[0]:
            continue
        print(f"[{w}] {str(k):8s} n={s[0]:6d}  型ラボ {s[1]/s[0]*100:5.2f}%  "
              f"3ヘッド {s[2]/s[0]*100:5.2f}%  差 {(s[2]-s[1])/s[0]*100:+.2f}pt")
print(f"（本番の重みは RANK_AXIS2_BAD_WEIGHT = {RANK_AXIS2_BAD_WEIGHT}）")
