#!/usr/bin/env python3
"""型別の商品設計に使う共有台を作る（2026-08-27）。

入力
  /tmp/honmei_attr.npz  … vintage walk-forward の 7車板
      PROB(N,210) 位置別合成PLの三連単確率 / PO(N,210) 予測オッズ(odds_tf_n7)
      WIN(N) 的中三連単のindex / PAY(N) 三連単の実払戻(円/100円)
      P3,PW(N,7) **vintage 予測** / LG,ST,PC(N,7) / A_line_pos,A_is_line_leader,
      A_prediction_mark,A_race_point,A_n_lines(N,7) / DATE,RTYPE,GRADE,VENUE,DAYI
  DB … 三連複の確定オッズ(35点)・`ex_left_behind_pct`

出力 /tmp/race_type_board.npz  （上記に加えて）
  TRIO_ODDS(N,35)   確定三連複オッズ（CANON3 の順）
  TRIO_WIN(N)       的中三連複の index / TRIO_PAY(N) その確定オッズ
  TRIO_PO(N,35)     **予測**三連複オッズ = 0.75 / Σ_perm (1/PO)
  BEHIND(N,7)       先頭の自力判定に使う遅れ率
  ARARE(N)          荒れ度スコア / TYPE(N) 型ラベル A〜F
  AGREE(N)          モデル上位2車 == 公式印◎○
  AXIS_SUM(N) GAP(N)

⚠️ 予測オッズ `odds_tf_n7` の train_end は 2025-12-31。**オッズを使う数字は 2026 窓で確認する**。
⚠️ P3/PW は vintage（honest）。型ラベルもそれで作る。
"""
from __future__ import annotations

import itertools
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import psycopg2

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)
from src.strategy_wt import RANK_7C_P3_SUM_MIN  # noqa: E402

CANON = list(itertools.permutations(range(1, 8), 3))
CANON3 = list(itertools.combinations(range(1, 8), 3))          # 35点
C3IDX = {frozenset(c): i for i, c in enumerate(CANON3)}
PAYBACK = 0.75
BEHIND_MID = 11.0

z = np.load("/tmp/honmei_attr.npz", allow_pickle=True)
KEY = [str(k) for k in z["KEY"]]
N = len(KEY)
print(f"vintage板 {N:,}R  {z['DATE'][0]}〜{z['DATE'][-1]}")

# ── 三連複の予測オッズ（三連単の予測板から導出）──
PO = z["PO"].astype(np.float64)
q3 = np.zeros((N, 35), np.float64)
for t, (a, b, c) in enumerate(CANON):
    j = C3IDX[frozenset((a, b, c))]
    with np.errstate(divide="ignore", invalid="ignore"):
        q3[:, j] += np.where(PO[:, t] > 0, 1.0 / PO[:, t], 0.0)
# 🔴 **`PAYBACK / q3` と書いてはいけない**（2026-08-28 是正）。
#    PO_perm = 払戻率/p_perm なので Σ_perm(1/PO) = P(trio)/払戻率。よって
#    三連複の予測オッズ = 払戻率/P(trio) = **1/Σ(1/PO)**。払戻率を掛けると
#    二重になり一律 0.75 倍ずれる（`build_type_lab_picks._fold_to_trio` の
#    docstring に同じ罠が書いてあるのに、こちらだけ直っていなかった）。
#    ⚠️ 現物の `/tmp/race_type_board.npz` は 1/q3 で作られている（実測: 保存値と
#       1/q3 の比が中央 1.0000000058）。この行が 0.75 のままだと、台を作り直した
#       瞬間に三連複の予測オッズが一律 25% 下がり、平均想定払戻ゲートも
#       `docs/type_lab/type_d.md` の数値も**エラー無しで別物になる**。
TRIO_PO = np.where(q3 > 0, 1.0 / q3, np.nan).astype(np.float32)

# ── DB: 三連複の確定オッズ と 遅れ率 ──
con = psycopg2.connect(os.environ["KEIRIN_DB_URL"])
cur = con.cursor()
TRIO_ODDS = np.full((N, 35), np.nan, np.float32)
BEHIND = np.zeros((N, 7), np.float32)
idx = {k: i for i, k in enumerate(KEY)}
for i0 in range(0, N, 2000):
    ch = KEY[i0:i0 + 2000]
    cur.execute("""SELECT race_key, combination, odds_value FROM keirin.wt_odds
                   WHERE bet_type='trio' AND race_key = ANY(%s)""", (ch,))
    for rk, comb, od in cur.fetchall():
        try:
            v = float(od)
        except (TypeError, ValueError):
            continue
        if not (0 < v < 9999):
            continue
        s = frozenset(int(x) for x in re.split(r"[-=→]", str(comb)))
        j = C3IDX.get(s)
        if j is not None:
            TRIO_ODDS[idx[rk], j] = v
    cur.execute("""SELECT race_key, frame_no, ex_left_behind_pct
                   FROM keirin.wt_entries WHERE race_key = ANY(%s)""", (ch,))
    for rk, fn, b in cur.fetchall():
        if 1 <= int(fn) <= 7:
            BEHIND[idx[rk], int(fn) - 1] = float(b or 0)
    print(f"  odds {min(i0+2000, N):,}/{N:,}", flush=True)
con.close()

# ── 的中三連複 ──
WIN = z["WIN"].astype(int)
TRIO_WIN = np.full(N, -1, np.int32)
ok = WIN >= 0
for i in np.flatnonzero(ok):
    TRIO_WIN[i] = C3IDX[frozenset(CANON[WIN[i]])]
TRIO_PAY = np.where(TRIO_WIN >= 0,
                    TRIO_ODDS[np.arange(N), np.clip(TRIO_WIN, 0, 34)], np.nan).astype(np.float32)

# ── 型ラベル（vintage P3 で作る）──
P3, PW = z["P3"], z["PW"]
LG, ST, LPOS, MARK, RP = z["LG"], z["ST"], z["A_line_pos"], z["A_prediction_mark"], z["A_race_point"]
DAYI = z["DAYI"].astype(int)
AXIS_SUM = np.zeros(N, np.float32); GAP = np.zeros(N, np.float32)
ARARE = np.zeros(N, np.int16); AGREE = np.zeros(N, bool)
TYPE = np.array([""] * N, dtype="<U1")
for i in range(N):
    p3 = P3[i]
    if not np.isfinite(p3).all():
        continue
    order = list(np.argsort(-p3) + 1)          # 車番（1-indexed）
    AXIS_SUM[i] = p3[order[0] - 1] + p3[order[1] - 1]
    oth = order[2:]
    GAP[i] = (p3[oth[0] - 1] + p3[oth[1] - 1]) / 2 - sum(p3[c - 1] for c in oth[2:]) / 3
    g = LG[i][order[0] - 1]
    mem = [c for c in range(1, 8) if LG[i][c - 1] == g] if g not in ("", "0") else []
    lead = next((c for c in mem if LPOS[i][c - 1] == 1), None)
    second = next((c for c in mem if LPOS[i][c - 1] == 2), None)
    size = len(mem) if mem else 1
    s = 1 if size == 2 else (-1 if size >= 4 else 0)
    if lead is not None:
        s += -1 if BEHIND[i][lead - 1] >= BEHIND_MID else 1
        s += 2 if ST[i][lead - 1] == "追" else 0
    s += DAYI[i] - 2
    if lead is not None and second is not None and RP[i][second - 1] > RP[i][lead - 1]:
        s += 1
    ARARE[i] = s
    AGREE[i] = set(order[:2]) == {c for c in range(1, 8) if MARK[i][c - 1] in (1, 2)}
    firm = AXIS_SUM[i] >= RANK_7C_P3_SUM_MIN
    TYPE[i] = ("A" if s <= -1 else "B" if s == 0 else "C") if firm else \
              ("D" if s <= -1 else "E" if s == 0 else "F")

out = {k: z[k] for k in z.files}
out.update(TRIO_ODDS=TRIO_ODDS, TRIO_PO=TRIO_PO, TRIO_WIN=TRIO_WIN, TRIO_PAY=TRIO_PAY,
           BEHIND=BEHIND, ARARE=ARARE, TYPE=TYPE, AGREE=AGREE,
           AXIS_SUM=AXIS_SUM, GAP=GAP)
np.savez_compressed("/tmp/race_type_board.npz", **out)
print("保存 /tmp/race_type_board.npz")
import collections
print("型:", dict(collections.Counter(TYPE[TYPE != ""])))
print("三連複オッズ充足:", f"{np.isfinite(TRIO_ODDS).all(1).mean()*100:.1f}%")
print("予測三連複オッズ 中央比(予測/確定):",
      f"{np.nanmedian(TRIO_PO[np.isfinite(TRIO_ODDS)] / TRIO_ODDS[np.isfinite(TRIO_ODDS)]):.3f}")
