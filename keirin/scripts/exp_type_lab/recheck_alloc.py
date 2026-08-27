#!/usr/bin/env python3
"""再検証① 配分方式 — ダッチ / 均等 / **信頼度傾斜**（2026-08-27・ユーザー提案）。

各型の採用案（SUMMARY.md）に対し、配分だけを差し替えて比べる。
判断指標は 件/日・表示的中・ガミ率・払戻中央・**払戻の分位**・平均想定払戻・2倍+/日。
🔴 ROI では採否を決めない。⚠️ 確認窓(2026)が本番相当（予測オッズの train_end が 2025-12-31）。
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path
from statistics import median

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

Z = None


def cache():
    global Z
    if Z is None:
        z = C.board()
        Z = {k: z[k] for k in ("PROB", "PO", "WIN", "PAY", "P3", "TRIO_PO",
                               "TRIO_ODDS", "TRIO_WIN", "DATE")}
    return Z


def build(i, kind, spec):
    """各型の**採用案どおり**に買い目を組む。

    spec:
      k        … 上限点数
      min_odds … 予測オッズの下限（帯）
      sigma    … Σ(1/予測オッズ) の上限（超えない範囲で確率上位から積む）
      drop_fav … 三連複で「相手のうち最人気1車を外す」（型D）
      axis2    … 三連複で軸2車固定（型D）
    """
    z = cache()
    if kind == "trio":
        po = z["TRIO_PO"][i]
        pr = {frozenset(c): 0.0 for c in C.CANON3}
        for t_, perm in enumerate(C.CANON):
            pr[frozenset(perm)] += float(z["PROB"][i][t_])
        if spec.get("axis2"):
            order = list(np.argsort(-z["P3"][i]) + 1)
            a1, a2 = int(order[0]), int(order[1])
            others = [int(c) for c in order[2:]]
            cs = [frozenset({a1, a2, c}) for c in others]
            cs = [c for c in cs if np.isfinite(po[C.C3IDX[c]]) and po[C.C3IDX[c]] > 0]
            if spec.get("drop_fav") and len(cs) > spec["k"]:
                cs.sort(key=lambda c: po[C.C3IDX[c]])      # 予測オッズ昇順＝人気順
                cs = cs[1:]                                 # 最人気1点を外す
            cs.sort(key=lambda c: -pr[c])
            return cs[:spec["k"]]
        cand = [frozenset(c) for c in C.CANON3
                if np.isfinite(po[C.C3IDX[frozenset(c)]])
                and po[C.C3IDX[frozenset(c)]] >= spec.get("min_odds", 0)]
        cand.sort(key=lambda c: -pr[c])
    else:
        po = z["PO"][i]
        cand = [C.CANON[t_] for t_ in range(210)
                if np.isfinite(po[t_]) and po[t_] >= spec.get("min_odds", 0)]
        cand.sort(key=lambda c: -z["PROB"][i][C.CIDX[c]])
    sig = spec.get("sigma")
    if sig is None:
        return cand[:spec["k"]]
    out, s = [], 0.0
    idxf = (lambda c: C.C3IDX[c]) if kind == "trio" else (lambda c: C.CIDX[c])
    for c in cand:
        o = float(po[idxf(c)])
        if o <= 0:
            continue
        if s + 1.0 / o > sig:
            continue
        out.append(c); s += 1.0 / o
        if len(out) >= spec["k"]:
            break
    return out


def stakes_of(i, kind, combos, mode, floor_mult=1.0):
    z = cache()
    if kind == "trio":
        odds = {c: float(z["TRIO_PO"][i][C.C3IDX[c]]) for c in combos}
        pr = C.trio_probs(i, combos)
    else:
        odds = {c: float(z["PO"][i][C.CIDX[c]]) for c in combos}
        pr = {c: float(z["PROB"][i][C.CIDX[c]]) for c in combos}
    if any((not np.isfinite(v)) or v <= 0 for v in odds.values()):
        return None, None
    if mode == "dutch":
        w = {c: 1.0 / odds[c] for c in combos}
        st = _alloc(w)
    elif mode == "equal":
        st = _alloc({c: 1.0 for c in combos})
    elif mode == "conf":
        st = C.confidence_stakes(odds, pr, floor_mult=floor_mult)
    else:
        raise ValueError(mode)
    return st, odds


def _alloc(w):
    n_units = C.BUDGET // C.UNIT
    if n_units < len(w):
        return None
    tot = sum(w.values())
    units = {k: 1 for k in w}
    rest = n_units - len(w)
    for k, v in w.items():
        units[k] += int(rest * v / tot)
    while sum(units.values()) < n_units:
        k = min(units, key=lambda x: units[x] / max(w[x], 1e-12))
        units[k] += 1
    return {k: v * C.UNIT for k, v in units.items()}


def run(type_label, window, kind, spec, mode, floor_mult=1.0, gate=True):
    z = cache()
    idx = C.select(type_label, window)
    nd = len(set(z["DATE"][idx]))
    recs = []
    for i in idx:
        combos = build(i, kind, spec)
        if len(combos) < spec.get("min_k", spec["k"]):
            continue
        st, odds = stakes_of(i, kind, combos, mode, floor_mult)
        if st is None:
            continue
        if gate:
            if min(odds.values()) < C.MIN_POINT_ODDS:
                continue
            mean = sum(st[c] * odds[c] for c in st) / len(st)
            if kind == "trio" and mean <= C.MIN_MEAN_PAYOUT:
                continue
        else:
            mean = sum(st[c] * odds[c] for c in st) / len(st)
        inv = sum(st.values())
        if kind == "trio":
            w = frozenset(C.CANON3[int(z["TRIO_WIN"][i])])
            pay = float(st[w] * z["TRIO_ODDS"][i][C.C3IDX[w]]) if w in st else 0.0
        else:
            w = C.CANON[int(z["WIN"][i])]
            pay = float(st[w] / 100.0 * z["PAY"][i]) if w in st else 0.0
        recs.append(dict(date=str(z["DATE"][i]), inv=inv, pay=pay, mean=mean, k=len(st)))
    return recs, nd


def show(label, recs, nd):
    if not recs:
        print(f"  {label:34s} (該当なし)")
        return
    hits = sorted(r["pay"] for r in recs if r["pay"] > 0)
    gami = sum(1 for r in recs if 0 < r["pay"] < r["inv"])
    inv = sum(r["inv"] for r in recs); pay = sum(r["pay"] for r in recs)
    means = sorted(r["mean"] for r in recs)
    two = sum(1 for r in recs if r["pay"] >= 2 * r["inv"])
    q = lambda p: hits[int(len(hits) * p)] if hits else 0
    print(f"  {label:34s} {len(recs)/nd:6.2f} {np.mean([r['k'] for r in recs]):5.1f}"
          f" {len(hits)/len(recs)*100:7.2f} {gami/max(len(hits),1)*100:6.2f}"
          f" {(len(hits)-gami)/len(recs)*100:8.2f}"
          f" {q(.25):8,.0f}/{median(hits) if hits else 0:8,.0f}/{q(.9):9,.0f}"
          f" {median(means):10,.0f} {two/nd:7.2f} {pay/inv*100:6.1f}")


HDR = ("  {:34s} {:>6s} {:>5s} {:>7s} {:>6s} {:>8s} {:>28s} {:>10s} {:>7s} {:>6s}"
       .format("腕", "件/日", "点数", "的中%", "ガミ%", "表示的中",
               "払戻 q25/中央/q90", "想定平均", "2倍+/日", "ROI%"))

#: 各型の採用案（`docs/type_lab/SUMMARY.md`）
PLANS = {
    "A": ("tf", {"k": 5, "sigma": 0.33, "min_k": 2}),
    "B": ("tf", {"k": 8, "sigma": 1 / 3.0, "min_k": 2}),
    "C": ("tf", {"k": 12, "min_odds": 20, "min_k": 12}),
    "D": ("trio", {"k": 4, "axis2": True, "drop_fav": True, "min_k": 4}),
    "E": ("tf", {"k": 14, "min_odds": 30, "min_k": 14}),
    "F": ("trio", {"k": 4, "min_odds": 10, "min_k": 4}),
}

if __name__ == "__main__":
    for t in (sys.argv[1:] or list("ABCDEF")):
        kind, spec = PLANS[t]
        print(f"\n=== 型{t}（{kind} 上限{spec['k']}点 / 帯 {spec.get('min_odds',0)}倍+ / "
              f"Σ上限 {spec.get('sigma')}）===")
        for w in ("explore", "confirm"):
            print(f"[{ '探索' if w=='explore' else '確認(本番相当)' }]")
            print(HDR)
            for mode, fm, lbl in (("dutch", 1.0, "ダッチ（現行案）"),
                                  ("equal", 1.0, "均等"),
                                  ("conf", 1.0, "信頼度傾斜（床=元返し）"),
                                  ("conf", 1.2, "信頼度傾斜（床=1.2倍）"),
                                  ("conf", 1.5, "信頼度傾斜（床=1.5倍）")):
                recs, nd = run(t, w, kind, spec, mode, fm)
                show(lbl, recs, nd)
