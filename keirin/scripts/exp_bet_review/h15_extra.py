#!/usr/bin/env python3
"""H15 補足: F_sign / B,C,D_sign の対象レースで「軸2車（p3 上位2車）が1-2着」だった割合と、そのときの当たり（台・2025）。"""
import pickle
import numpy as np
from h01_common import *   # noqa
import lineup_sim as S
import h15_run as H
b = load_board_2025(); S._Z = {k: b[k] for k in S._NEED}; z = S.board()
m = ((z["TYPE"] != "") & (z["TRIO_WIN"] >= 0) & np.isfinite(z["TRIO_PAY"]) & z["OKPRED"] & (z["WIN"] >= 0) & np.isfinite(z["PAY"]))
idx = [int(i) for i in np.flatnonzero(m)]
cache = {i: S.ctx(i) for i in idx}; ok = [i for i in idx if cache[i] is not None]
byk = {cache[i].key: cache[i] for i in ok}
res = pickle.load(open(D / "h15_result.pkl", "rb"))
recs = res["recs"]
def stat(rows, name, t):
    n = len(rows); two = 0; two_miss = 0; hit = 0; first_two_inv = 0
    base_two = 0
    for r in rows:
        x = byk[r["race_key"]]
        a1, a2 = x.shape.order[:2]
        w = x.win_tf
        both = {w[0], w[1]} == {a1, a2}
        two += both
        hit += r["pay"] > 0
        two_miss += both and r["pay"] == 0
    print(f"{name}: R={n} 軸2車が1-2着(順不同)={two}({two/n*100:.1f}%) その中で外れ={two_miss}({two_miss/max(two,1)*100:.1f}%) 当たり={hit}({hit/n*100:.1f}%) 軸2車1-2着で外れ/全R={two_miss/n*100:.1f}%")
for nm, f in (("F_sign", lambda r: r["plan"] == "F_sign"), ("B/C/D_sign", lambda r: r["plan"] in ("B_sign", "C_sign", "D_sign"))):
    stat([r for r in recs if f(r)], nm, None)
# 対照: 同じレースの hit
for arm, f in (("F", lambda r: r["plan"] == "F_sign"), ("BCD", lambda r: r["plan"] in ("B_sign", "C_sign", "D_sign"))):
    rows = [r for r in recs if f(r)]
    hh = []
    for r in rows:
        x = byk[r["race_key"]]
        h, why = H.hit_counterpart(x, r["plan"][0])
        if h: hh.append(dict(race_key=r["race_key"], pay=h["pay"]))
    stat(hh, arm + "_hit(同レース)", None)
