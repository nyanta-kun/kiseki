#!/usr/bin/env python3
"""厳選AI(614)の実入稿 vs 自社の三連単の組み立て — 同一レース上の直接対決。

台: /tmp/honmei_attr.npz（vintage walk-forward の 210点板・7車）
    PROB=位置別合成PLの買い目確率 / PO=予測オッズ(odds_tf_n7, train_end 2025-12-31)
    PAY=実払戻(円/100円)
⚠️ 予測オッズは 2026 が out-of-sample。**2026 窓だけを読むこと**。
"""
from __future__ import annotations
import itertools, json, os, sys
from collections import defaultdict
from pathlib import Path
from statistics import median
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO)); os.chdir(REPO)
from src.strategy_wt import (rank_7t1_select, rank_7t1_stakes,   # noqa: E402
                             rank_7t1_is_cross_line, RANK_7T3_MIN_ODDS)

CANON = list(itertools.permutations(range(1, 8), 3))
CIDX = {c: i for i, c in enumerate(CANON)}
BUDGET, UNIT = 10_000, 100

z = np.load("/tmp/honmei_attr.npz", allow_pickle=True)
KEY = list(z["KEY"]); idx = {k: i for i, k in enumerate(KEY)}
PROB, PO, WIN, PAY = z["PROB"], z["PO"], z["WIN"], z["PAY"]
P3, PW = z["P3"], z["PW"]
LG, DATE, RTYPE = z["LG"], z["DATE"], z["RTYPE"]
LPOS, LEADER = z["A_line_pos"], z["A_is_line_leader"]
OK = z["OKPRED"]

G = Path(__file__).resolve().parent / "gensen" / "joined.jsonl"
rows = [json.loads(l) for l in G.open(encoding="utf-8")]
lo, hi = (sys.argv[1], sys.argv[2]) if len(sys.argv) >= 3 else ("20260101", "20260826")

def their_points(r):
    pts = {}
    for l in r["legs"]:
        if l["bet_type"] != "3連単" or len(l["cols"]) < 3:
            continue
        u = l["unit"] or 0
        for c in itertools.product(*l["cols"]):
            if len(set(c)) == 3:
                pts[c] = pts.get(c, 0) + u
    return pts

def payout_of(i, pts):
    w = int(WIN[i])
    if w < 0: return 0.0
    combo = CANON[w]
    s = pts.get(combo, 0)
    return PAY[i] * s / 100.0 if s else 0.0

def summarize(name, recs, ndays):
    inv = sum(r["inv"] for r in recs); pay = sum(r["pay"] for r in recs)
    hits = [r for r in recs if r["pay"] > 0]
    gami = [h for h in hits if h["pay"] < h["inv"]]
    ps = sorted(h["pay"] for h in hits)
    big = {t: sum(1 for p in ps if p >= t) for t in (30_000, 100_000, 150_000, 300_000)}
    print(f"{name:26s} n={len(recs):5d} 点{np.mean([r['k'] for r in recs]):4.2f} "
          f"的中 {len(hits)/len(recs)*100:5.2f}% 表示 {(len(hits)-len(gami))/len(recs)*100:5.2f}% "
          f"ROI {pay/inv*100:6.1f}% 中央 {median(ps) if ps else 0:>8,.0f} "
          + " ".join(f"{t//10000}万+{big[t]/ndays:5.3f}/日" for t in big))

recs = defaultdict(list)
used_dates = set()
n_skip = 0
for r in rows:
    if not (lo <= r["date"] <= hi):
        continue
    k = r["race_key"]
    i = idx.get(k)
    if i is None or not OK[i] or int(WIN[i]) < 0 or not np.isfinite(PAY[i]):
        n_skip += 1; continue
    pts = their_points(r)
    if not pts:
        n_skip += 1; continue
    used_dates.add(r["date"])
    # --- 彼ら ---
    recs["彼ら(実入稿)"].append(dict(inv=sum(pts.values()), pay=payout_of(i, pts), k=len(pts), key=k))
    # --- 自社A: PL top1 に全額 ---
    j = int(np.argmax(PROB[i]))
    recs["自社A PL top1・1点"].append(dict(inv=BUDGET, pay=payout_of(i, {CANON[j]: BUDGET}), k=1, key=k))
    # --- 自社A2: PL top2 に等分 ---
    j2 = list(np.argsort(-PROB[i])[:2])
    recs["自社A2 PL top2・2点"].append(dict(
        inv=BUDGET, pay=payout_of(i, {CANON[a]: BUDGET // 2 for a in j2}), k=2, key=k))
    # --- 自社B: 7T1 の組み立て（同じレースに機械的に適用）---
    p3 = {c + 1: float(P3[i, c]) for c in range(7)}
    pw = {c + 1: float(PW[i, c]) for c in range(7)}
    po = {CANON[t]: float(PO[i, t]) for t in range(210)}
    sel = rank_7t1_select(p3, pw, po)
    if sel:
        legs = sel[2]
        st = rank_7t1_stakes(legs)
        pp = {tuple(int(x) for x in leg.split("-")): st[leg] for leg in legs}
        recs["自社B 7T1の組み立て"].append(dict(inv=sum(pp.values()), pay=payout_of(i, pp), k=len(pp), key=k))
    # --- 自社C: 予測30倍以上のうちPL上位5点（7T3の組み立て）---
    cand = [t for t in range(210) if PO[i, t] >= RANK_7T3_MIN_ODDS]
    if cand:
        cand.sort(key=lambda t: -PROB[i, t])
        sel5 = cand[:5]
        s = BUDGET // len(sel5) // UNIT * UNIT
        pp = {CANON[t]: s for t in sel5}
        recs["自社C 7T3の組み立て"].append(dict(inv=sum(pp.values()), pay=payout_of(i, pp), k=len(pp), key=k))

nd = len(used_dates)
print(f"\n=== 同一レース直接対決  {lo}〜{hi}  対象 {len(recs['彼ら(実入稿)']):,}R / {nd}日 "
      f"(台に無い・7車外・払戻不明 {n_skip} は除外) ===")
for name in ("彼ら(実入稿)", "自社A PL top1・1点", "自社A2 PL top2・2点",
             "自社B 7T1の組み立て", "自社C 7T3の組み立て"):
    if recs[name]:
        summarize(name, recs[name], nd)
