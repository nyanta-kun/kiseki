#!/usr/bin/env python3
"""05 市場の動き: 台 + DB(読み取りのみ) から検証台を作る。

出力 /private/tmp/.../05_market_drift/market.npz
  板 (/tmp/race_type_board.npz) の全行 N と同じ順で
    START(N) 発走unix / FIRST_H(N) その開催の第1R発走時(JST) / WAVE(N) morning|noon|night
  板のうち DATE>=2026-06-08 の行 (n2) について
    IDX2(n2) 板index / SNAP(n2,T,35) 三連複スナップショット（9999.9=未確定→nan, 無し→nan）
    SNAP_AT(n2,T) 取得時刻 'HH:MM:SS' ('' なら無し) / TF_FILL(n2,T) 三連単の確定点数(210点中)
  T = SNAP_TYPES の順
DB は読み取りのみ。
"""
from __future__ import annotations
import itertools, os, re, sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import numpy as np, psycopg2

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "market.npz")
SNAP_TYPES = ["morning", "h10", "h12", "h14", "h18", "h20", "evening"]
CANON3 = list(itertools.combinations(range(1, 8), 3))
C3IDX = {frozenset(c): i for i, c in enumerate(CANON3)}
JST = timezone(timedelta(hours=9))

z = np.load("/tmp/race_type_board.npz", allow_pickle=True)
KEY = [str(k) for k in z["KEY"]]; DATE = z["DATE"]; N = len(KEY)
idx = {k: i for i, k in enumerate(KEY)}
print("board", N)

con = psycopg2.connect(os.environ["KEIRIN_DB_URL"])
cur = con.cursor()
cur.execute("SET statement_timeout = 110000")

# ── 発走時刻・波 ──
START = np.zeros(N, np.int64)
venue_date_first = defaultdict(lambda: 10**12)
rows_all = []
for i0 in range(0, N, 3000):
    ch = KEY[i0:i0+3000]
    cur.execute("SELECT race_key, venue_id, race_date, start_at FROM keirin.wt_races WHERE race_key = ANY(%s)", (ch,))
    rows_all += cur.fetchall()
# 第1Rの発走は開催(会場×日)の全レースで決まる（板に無いレースも含む）
dv = sorted({(r[2], r[1]) for r in rows_all})
first = {}
for j0 in range(0, len(dv), 500):
    chunk = dv[j0:j0+500]
    cur.execute("""SELECT race_date, venue_id, MIN(start_at::bigint) FROM keirin.wt_races
                   WHERE (race_date, venue_id) IN %s AND cancel=0 GROUP BY 1,2""", (tuple(chunk),))
    for d, v, st in cur.fetchall():
        first[(d, v)] = int(st)
FIRST_H = np.full(N, -1, np.int16)
for rk, v, d, st in rows_all:
    i = idx[rk]
    START[i] = int(st) if st else 0
    f = first.get((d, v))
    if f:
        FIRST_H[i] = datetime.fromtimestamp(f, JST).hour
WAVE = np.where(FIRST_H >= 18, "night", np.where(FIRST_H >= 12, "noon", "morning")).astype("<U8")
print("wave", {w: int((WAVE == w).sum()) for w in ("morning", "noon", "night")})

# ── スナップショット（2026-06-08 以降） ──
IDX2 = np.flatnonzero(DATE >= "2026-06-08")
n2 = len(IDX2); T = len(SNAP_TYPES); tix = {t: j for j, t in enumerate(SNAP_TYPES)}
SNAP = np.full((n2, T, 35), np.nan, np.float32)
SNAP_AT = np.full((n2, T), "", dtype="<U8")
TF_FILL = np.full((n2, T), -1, np.int16)
pos2 = {KEY[i]: j for j, i in enumerate(IDX2)}
keys2 = [KEY[i] for i in IDX2]
for i0 in range(0, n2, 400):
    ch = keys2[i0:i0+400]
    cur.execute("""SELECT race_key, snapshot_type, combination, odds_value, snapshot_at
                   FROM keirin.wt_odds_snapshot WHERE bet_type='trio' AND race_key = ANY(%s)""", (ch,))
    for rk, st, comb, od, at in cur.fetchall():
        j = tix.get(st)
        if j is None:
            continue
        r = pos2[rk]
        SNAP_AT[r, j] = at.strftime("%H:%M:%S")
        try:
            v = float(od)
        except (TypeError, ValueError):
            continue
        if not (0 < v < 9999):
            continue
        s = frozenset(int(x) for x in re.split(r"[-=→]", str(comb)))
        c = C3IDX.get(s)
        if c is not None:
            SNAP[r, j, c] = v
    cur.execute("""SELECT race_key, snapshot_type,
                          SUM(CASE WHEN odds_value > 0 AND odds_value < 9999 THEN 1 ELSE 0 END)
                   FROM keirin.wt_odds_snapshot WHERE bet_type='trifecta' AND race_key = ANY(%s)
                   GROUP BY 1,2""", (ch,))
    for rk, st, n in cur.fetchall():
        j = tix.get(st)
        if j is not None:
            TF_FILL[pos2[rk], j] = int(n)
    print(f"  snap {min(i0+400, n2)}/{n2}", flush=True)
con.close()
np.savez_compressed(OUT, START=START, FIRST_H=FIRST_H, WAVE=WAVE, IDX2=IDX2, SNAP=SNAP,
                    SNAP_AT=SNAP_AT, TF_FILL=TF_FILL, SNAP_TYPES=np.array(SNAP_TYPES))
print("saved", OUT, "n2", n2)
