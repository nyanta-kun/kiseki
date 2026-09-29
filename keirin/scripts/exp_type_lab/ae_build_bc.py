"""A_hit / E_hit の代替腕と事前特徴をレースごとに作る（2026-09-29）。修正後の台（ctx_fixed）。"""
import os
import pickle
import sys
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lineup_sim import board, build, gate_ok, settle
from src.type_lab import PLANS

HERE = Path(__file__).resolve().parent
rows = [r for r in pickle.load(open(HERE / "two_axis_cond_rows.pkl", "rb")) if r["used"] in ("B_hit", "C_hit")]
cache = pickle.load(open(os.environ.get("TYPE_LAB_CTX", "/tmp/type_lab_ctx_fixed.pkl"), "rb"))
z = board()
BANDS = [0, 3, 5, 10, 20, 30, 50, 100, 300, 1e9]
def bnd(o):
    for j in range(len(BANDS) - 1):
        if BANDS[j] <= o < BANDS[j + 1]: return j
    return len(BANDS) - 2
# 較正: 予測オッズ帯ごとの 実測/モデル（探索窓・7車全レース・全210目）
hit = defaultdict(float); ps = defaultdict(float)
for x in cache.values():
    if x is None or not ("2024-07-01" <= x.date <= "2025-12-31"): continue
    for c, o in x.po_tf.items():
        b = bnd(o); ps[b] += float(x.pr_tf.get(c, 0.0)); hit[b] += c == x.win_tf
CAL = {b: hit[b] / ps[b] for b in ps if ps[b] > 0}
print("CAL", {BANDS[b]: round(v, 2) for b, v in sorted(CAL.items())})
def calp(x, c): return float(x.pr_tf.get(c, 0.0)) * CAL.get(bnd(x.po_tf[c]), 1.0)
ARMS = {**{k: {f"T{t//1000}k": replace(PLANS[k], target=t) for t in (20000, 25000, 30000, 40000)} for k in ("B_hit", "C_hit")},
        "E_hit": {**{f"lo{m}": replace(PLANS["E_hit"], min_odds=float(m)) for m in (10, 15, 20)},
                  "F12": PLANS["F_hit"]}}
out = []
for n, r in enumerate(rows):
    x = cache[r["i"]]; i = r["i"]
    f = {}
    cal = {c: calp(x, c) for c in x.po_tf}
    tot = sum(cal.values())
    for lim in (5, 10, 30):
        f[f"pc{lim}"] = sum(v for c, v in cal.items() if x.po_tf[c] < lim) / tot
    cheap = sorted(((v, c) for c, v in cal.items() if x.po_tf[c] < 30), reverse=True)
    f["top_cheap_p"] = cheap[0][0] / tot if cheap else 0.0
    f["n_cheap5"] = sum(1 for c in x.po_tf if x.po_tf[c] < 5)
    f["min_po"] = min(x.po_tf.values())
    inv = sorted((1 / o for o in x.po_tf.values()), reverse=True)
    f["mkt_top5"] = sum(inv[:5]) / sum(inv)
    p3 = np.array(z["P3"][i], float); rp = np.array(z["A_race_point"][i], float)
    f["p3_min"] = float(np.nanmin(p3)); f["n_weak10"] = int(np.sum(p3 < 0.10)); f["n_weak15"] = int(np.sum(p3 < 0.15))
    srt = np.sort(p3)[::-1]; f["p3_gap_bottom"] = float(srt[4] - srt[6])   # 5位と7位の差
    f["p3_top3_sum"] = float(srt[:3].sum())
    rpv = rp[np.isfinite(rp) & (rp > 0)]
    f["rp_gap_min"] = float(np.median(rpv) - rpv.min()) if len(rpv) >= 3 else 0.0
    lines = x.shape.lines; a1, a2 = x.shape.order[:2]
    f["n_lines"] = len(lines); f["max_line"] = max((len(l) for l in lines), default=1)
    ln = next((l for l in lines if a1 in l), ())
    f["ax_same"] = int(bool(ln) and a2 in ln); f["ax_line3"] = int(len(ln) >= 3)
    st = [str(s) for s in z["ST"][i]]
    f["ax_lead_nige"] = int(bool(ln) and st[ln[0] - 1] == "逃")
    f["n_nige"] = sum(1 for s in st if s == "逃")
    f["single_nige"] = int(f["n_nige"] == 1)
    f["axis_sum"] = r["axis_sum"]; f["pw_ent"] = r["pw_ent"]; f["win_gap"] = r["win_gap"]
    r = dict(r, f=f, arms={})
    for a, pl in ARMS[r["used"]].items():
        g = build(x, pl)
        if g and gate_ok(g[0], g[1], g[3]):
            iv, py = settle(x, g[0], False); r["arms"][a] = (iv, py, len(g[0]))
        else:
            r["arms"][a] = None
    out.append(r)
    if n % 2000 == 0: print(n, len(rows), flush=True)
pickle.dump(out, open(HERE / "ae_rows_bc.pkl", "wb"))
print("done", len(out))
