#!/usr/bin/env python3
"""E の記述分析用に出走表属性を台へ足す（2026-08-26・読み取りのみ）。"""
from __future__ import annotations
import os, sys, time
from collections import defaultdict
import numpy as np
import psycopg2

z = np.load("/tmp/honmei_tf.npz", allow_pickle=True)
KEY = z["KEY"]; idx = {k: i for i, k in enumerate(KEY)}
N = len(KEY)
t0 = time.time()

con = psycopg2.connect(os.environ["KEIRIN_DB_URL"])
cur = con.cursor()
COLS = ["line_group", "line_size", "line_pos", "is_line_leader", "prediction_mark",
        "player_class", "style", "race_point", "n_lines"]
A = {c: np.full((N, 7), np.nan, np.float32) for c in COLS if c not in ("line_group", "style", "player_class")}
LG = np.full((N, 7), "", dtype=object)
ST = np.full((N, 7), "", dtype=object)
PC = np.full((N, 7), "", dtype=object)
keys = list(KEY)
for i in range(0, len(keys), 3000):
    cur.execute("""
     select race_key, frame_no, line_group, line_size, line_pos, is_line_leader,
            prediction_mark, player_class, style, race_point, n_lines
     from keirin.wt_entries where race_key = any(%s)""", (keys[i:i + 3000],))
    for (rk, fn, lg, lsz, lp, ld, mk, cls_, sty, rp, nl) in cur.fetchall():
        r = idx.get(rk); c = int(fn) - 1
        if r is None or not (0 <= c < 7):
            continue
        LG[r, c] = lg or ""; ST[r, c] = sty or ""; PC[r, c] = cls_ or ""
        A["line_size"][r, c] = lsz if lsz is not None else np.nan
        A["line_pos"][r, c] = lp if lp is not None else np.nan
        A["is_line_leader"][r, c] = 1.0 if ld else 0.0
        A["prediction_mark"][r, c] = mk if mk is not None else np.nan
        A["race_point"][r, c] = rp if rp is not None else np.nan
        A["n_lines"][r, c] = nl if nl is not None else np.nan
    if i % 15000 == 0:
        print(f"  {i:,}/{len(keys):,}  {time.time()-t0:.0f}s", flush=True)

cur.execute("""
 select race_key, coalesce(grade,''), coalesce(cup_grade::text,''),
        coalesce(day_index,0), coalesce(n_entries,0)
 from keirin.wt_races where race_key = any(%s)""", (keys,))
GRADE = np.full(N, "", dtype=object); CUPG = np.full(N, "", dtype=object)
DAYI = np.zeros(N, np.int16)
for rk, gr, cg, di, ne in cur.fetchall():
    r = idx.get(rk)
    if r is None: continue
    GRADE[r] = gr; CUPG[r] = cg; DAYI[r] = di
con.close()

out = {k: z[k] for k in z.files}
out.update({f"A_{k}": v for k, v in A.items()})
out["LG"] = LG.astype(str); out["ST"] = ST.astype(str); out["PC"] = PC.astype(str)
out["GRADE2"] = GRADE.astype(str); out["CUPG2"] = CUPG.astype(str); out["DAYI"] = DAYI
np.savez_compressed("/tmp/honmei_attr.npz", **out)
print(f"保存 /tmp/honmei_attr.npz  {time.time()-t0:.0f}s  "
      f"line_group 充足 {np.mean(LG!='')*100:.1f}%  style 充足 {np.mean(ST!='')*100:.1f}%  "
      f"n_lines 充足 {np.mean(np.isfinite(A['n_lines']))*100:.1f}%")
