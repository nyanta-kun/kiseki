#!/usr/bin/env python3
"""台からレース単位の表を作る（軸差し替え検証 10）。

1レース1行: 日付・窓・型・pw/p3 の順位・印(◎○▲△)・予測オッズから導いた
市場の1着シェア/3着内シェア・決着(1,2,3着)・各種「軸1の信頼」量。
"""
from __future__ import annotations
import pickle, sys
import numpy as np
sys.path.insert(0, "/Users/ysuzuki/GitHub/kiseki/keirin/scripts/exp_type_lab")
import common as C

OUT = "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/da42d5d7-4330-448a-8026-572403e4f747/scratchpad/fleet/10_axis_swap/table.pkl"

z = C.board()
PW, P3 = z["PW"], z["P3"]
MARK = z["A_prediction_mark"]
PO, TRIO_PO = z["PO"], z["TRIO_PO"]
WIN, DATE, TYPE, AXIS_SUM = z["WIN"], z["DATE"], z["TYPE"], z["AXIS_SUM"]
RP = z["A_race_point"]
CANON, CANON3 = C.CANON, C.CANON3
first_of = np.array([p[0] - 1 for p in CANON])            # perm -> 1着車(0-6)
in_perm = np.zeros((210, 7), bool)
for j, p in enumerate(CANON):
    for c in p:
        in_perm[j, c - 1] = True
in_trio = np.zeros((35, 7), bool)
for j, t in enumerate(CANON3):
    for c in t:
        in_trio[j, c - 1] = True

idx_all = C.select(window="all")
rows = []
for i in idx_all:
    pw, p3 = PW[i].astype(float), P3[i].astype(float)
    if not (np.isfinite(pw).all() and np.isfinite(p3).all()):
        continue
    po = PO[i].astype(float)
    tpo = TRIO_PO[i].astype(float)
    if not (np.isfinite(po).all() and (po > 0).all()):
        continue
    inv = 1.0 / po
    mk_win = np.array([inv[first_of == c].sum() for c in range(7)])   # 市場の1着シェア
    mk_win /= mk_win.sum()
    if np.isfinite(tpo).all() and (tpo > 0).all():
        tinv = 1.0 / tpo
        mk_p3 = np.array([tinv[in_trio[:, c]].sum() for c in range(7)])
        mk_p3 /= mk_p3.sum() / 3.0
    else:
        mk_p3 = np.full(7, np.nan)
    mark = MARK[i].astype(int)
    hon = int(np.flatnonzero(mark == 1)[0]) + 1 if (mark == 1).any() else 0
    tai = int(np.flatnonzero(mark == 2)[0]) + 1 if (mark == 2).any() else 0
    san = int(np.flatnonzero(mark == 3)[0]) + 1 if (mark == 3).any() else 0
    perm = CANON[int(WIN[i])]
    pwn = pw / pw.sum()
    ent = float(-(pwn * np.log(pwn)).sum())
    pw_o = list(np.argsort(-pw) + 1)
    p3_o = list(np.argsort(-p3) + 1)
    d = str(DATE[i])
    rows.append(dict(
        i=int(i), date=d, win="explore" if d <= "2025-12-31" else "confirm",
        type=str(TYPE[i]), pw=pw, p3=p3, pwn=pwn, mk_win=mk_win, mk_p3=mk_p3,
        hon=hon, tai=tai, san=san, fin=perm,
        pw_o=pw_o, p3_o=p3_o,
        pw_max=float(pwn.max()), pw_gap12=float(np.sort(pwn)[-1] - np.sort(pwn)[-2]),
        p3_gap12=float(np.sort(p3)[-1] - np.sort(p3)[-2]),
        pw_ent=ent, axis_sum=float(AXIS_SUM[i]),
        rp_sd=float(np.std(RP[i])) if np.isfinite(RP[i]).all() else np.nan,
    ))
print(f"rows={len(rows):,}  explore={sum(r['win']=='explore' for r in rows):,}"
      f"  confirm={sum(r['win']=='confirm' for r in rows):,}")
pickle.dump(rows, open(OUT, "wb"))
