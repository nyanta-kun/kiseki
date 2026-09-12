#!/usr/bin/env python3
"""「堅い×飛びそう」を選別できた（OOS AUC 0.675）先で、軸を外して買うと何が起きるか。

- 選別モデルは firm_bust_select.py と同一（学習=探索窓のみ）。**逆向き学習でも確かめる**。
- 買い方: 軸1（複勝率1位）を含まない三連単のうちモデル確率の上位k点をダッチ。
  本番と同じ入稿ゲート（1点でも予測<2.0倍は見送り / 想定払戻の平均<=2万円は見送り）。
- 🔴 同じ件数の**無作為対照20本**と比べる。
"""
from __future__ import annotations
import itertools, json, sys
from pathlib import Path
from statistics import median
import numpy as np
import lightgbm as lgb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from firm_bust_select import (X, names, M, AX, FIRM, DATE, PAY, BUST, BIG, KEY,
                              P3, a1, z, auc, FEATS)  # noqa

CANON = list(itertools.permutations(range(1, 8), 3))
PROB, PO, WIN = z["PROB"].astype(float), z["PO"].astype(float), z["WIN"]
BUDGET, UNIT, MIN_POINT_ODDS, MIN_MEAN_PAYOUT = 10_000, 100, 2.0, 20_000

EXP = (DATE >= "2024-07-01") & (DATE <= "2025-12-31")
CNF = (DATE >= "2026-01-01")

def fit(mask):
    ds = lgb.Dataset(X[mask], label=BUST[mask].astype(int), feature_name=names)
    return lgb.train(dict(objective="binary", learning_rate=0.05, num_leaves=15,
                          min_data_in_leaf=200, feature_fraction=0.8, bagging_fraction=0.8,
                          bagging_freq=1, verbose=-1, seed=0), ds, num_boost_round=250)

print("■ 向きの安定性（学習窓を入れ替えても同じか）")
for tr_name, tr, te_name, te in [("探索", M & (AX >= FIRM) & EXP, "確認", M & (AX >= FIRM) & CNF),
                                 ("確認", M & (AX >= FIRM) & CNF, "探索", M & (AX >= FIRM) & EXP)]:
    mdl = fit(tr)
    s = mdl.predict(X[te])
    idx = np.flatnonzero(te)
    hi = idx[s >= np.quantile(s, 0.9)]
    print(f"  学習{tr_name}→評価{te_name}: OOS AUC {auc(s, BUST[te]):.3f}  "
          f"上位10%の軸崩壊 {BUST[hi].mean()*100:.1f}% (基準 {BUST[te].mean()*100:.1f}%)  "
          f"10万+ {BIG[hi].mean()*100:.2f}% (基準 {BIG[te].mean()*100:.2f}%)  "
          f"飛んだ時の払戻中央 {np.median(PAY[hi][BUST[hi]]):,.0f}円")

# ── 買い（確認窓・honest）──
mdl = fit(M & (AX >= FIRM) & EXP)
TE = M & (AX >= FIRM) & CNF
idx = np.flatnonzero(TE)
score = mdl.predict(X[TE])

def buy(i: int, k: int, exclude_axis: bool) -> dict | None:
    """i のレースで k 点買う。exclude_axis なら軸1を含まない目だけから選ぶ。"""
    cars = set(range(1, 8))
    ok = [t for t, c in enumerate(CANON)
          if (not exclude_axis) or (a1[i] + 1) not in c]
    ok = [t for t in ok if np.isfinite(PO[i, t]) and PO[i, t] > 0]
    if len(ok) < k:
        return None
    ok.sort(key=lambda t: -PROB[i, t])
    sel = ok[:k]
    w = np.array([1.0 / PO[i, t] for t in sel])
    units = np.ones(k, int)
    rest = BUDGET // UNIT - k
    if rest < 0:
        return None
    add = (rest * w / w.sum()).astype(int)
    units += add
    while units.sum() < BUDGET // UNIT:
        j = int(np.argmin(units / w))
        units[j] += 1
    st = {t: int(u) * UNIT for t, u in zip(sel, units)}
    po = [PO[i, t] for t in st]
    if min(po) < MIN_POINT_ODDS:
        return None
    mean = sum(st[t] * PO[i, t] for t in st) / len(st)
    if mean <= MIN_MEAN_PAYOUT:
        return None
    w_t = int(WIN[i])
    pay = float(st[w_t] / 100.0 * PAY[i]) if w_t in st else 0.0
    return dict(date=str(DATE[i]), inv=float(sum(st.values())), pay=pay, k=k, mean=mean)

def agg(recs, ndays):
    if not recs:
        return None
    inv = sum(r["inv"] for r in recs); pay = sum(r["pay"] for r in recs)
    hits = [r for r in recs if r["pay"] > 0]
    shown = [r for r in hits if r["pay"] >= r["inv"]]
    pays = sorted(r["pay"] for r in hits)
    return dict(n=len(recs), perday=len(recs) / ndays, hit=len(hits) / len(recs) * 100,
                shown=len(shown) / len(recs) * 100, roi=pay / inv * 100,
                med=median(pays) if pays else 0,
                big=sum(1 for p in pays if p >= 100_000) / ndays,
                big3=sum(1 for p in pays if p >= 300_000) / ndays)

NDAYS = len(set(DATE[TE]))
print(f"\n■ 確認窓(2026)の堅いレース {TE.sum():,}R / {NDAYS}日 で「軸1を外して買う」")
print(f"  {'腕':38s} {'件/日':>6s} {'的中%':>6s} {'表示的中%':>9s} {'払戻中央':>9s} "
      f"{'10万+/日':>8s} {'30万+/日':>8s} {'ROI%':>7s}")
rng = np.random.default_rng(0)
for k in (6, 10, 14):
    for topfrac in (0.10, 0.20):
        thr = np.quantile(score, 1 - topfrac)
        sel = idx[score >= thr]
        recs = [r for r in (buy(i, k, True) for i in sel) if r]
        s = agg(recs, NDAYS)
        if s:
            print(f"  選別上位{int(topfrac*100):2d}% 軸外し{k:2d}点            "
                  f"{s['perday']:6.2f} {s['hit']:6.2f} {s['shown']:9.2f} {s['med']:9,.0f} "
                  f"{s['big']:8.3f} {s['big3']:8.3f} {s['roi']:7.1f}")
        # 無作為対照
        ctrl = []
        for seed in range(20):
            r2 = np.random.default_rng(seed).choice(idx, size=len(sel), replace=False)
            rc = [r for r in (buy(i, k, True) for i in r2) if r]
            cs = agg(rc, NDAYS)
            if cs:
                ctrl.append(cs)
        if ctrl and s:
            def med_of(key):
                return float(np.median([c[key] for c in ctrl]))
            wins = {key: int(sum(s[key] > c[key] for c in ctrl)) for key in
                    ("shown", "roi", "big")}
            print(f"    └無作為同数(中央)                  "
                  f"{med_of('perday'):6.2f} {med_of('hit'):6.2f} {med_of('shown'):9.2f} "
                  f"{med_of('med'):9,.0f} {med_of('big'):8.3f} {med_of('big3'):8.3f} "
                  f"{med_of('roi'):7.1f}   勝ち 表示的中{wins['shown']}/20 "
                  f"ROI{wins['roi']}/20 10万+{wins['big']}/20")
