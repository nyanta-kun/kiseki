#!/usr/bin/env python3
"""再検証② **既存ゲートを外した**全面置き換え前提の設計（2026-08-27・ユーザー指示）。

> 現在の検証は全レースを網羅的に計っているので、既存商品の全面置き換えのベースと考えている。
> 既存商品でのゲートなどは気にせず検証して。

したがって `MIN_POINT_ODDS=2.0` も `MIN_MEAN_PAYOUT=20,000` も**掛けない**。
代わりに**ガミを出さないこと自体を配分で担保する**（ユーザー提案の信頼度傾斜）:

  ① 各点に floor_i = 予算×floor_mult ÷ 予測オッズ_i（当たっても投資を下回らない最低額）
  ② 残りを**確率に比例**して配る（自信のある点ほど厚い）

比較対象: ダッチ（払戻を全点で揃える）/ 均等。
判断指標: 表示的中（ガミ除く）・ガミ率・払戻の分位・2倍+/日・件/日。ROI は参考。
⚠️ 確認窓(2026)が本番相当（予測オッズ `odds_tf_n7` の train_end が 2025-12-31）。
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path
from statistics import median

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

_C = {}


def cache():
    if not _C:
        z = C.board()
        for k in ("PROB", "PO", "WIN", "PAY", "P3", "TRIO_PO", "TRIO_ODDS",
                  "TRIO_WIN", "DATE", "TYPE"):
            _C[k] = z[k]
        # 三連複の確率（三連単板を6順列ぶん畳む）
        P3C = np.zeros((len(_C["PROB"]), 35), np.float32)
        for t, perm in enumerate(C.CANON):
            P3C[:, C.C3IDX[frozenset(perm)]] += _C["PROB"][:, t]
        _C["TRIO_PROB"] = P3C
    return _C


def pick(i, kind, min_odds, k):
    z = cache()
    if kind == "trio":
        po, pr = z["TRIO_PO"][i], z["TRIO_PROB"][i]
        n = 35
    else:
        po, pr = z["PO"][i], z["PROB"][i]
        n = 210
    ok = np.isfinite(po) & (po >= max(min_odds, 1e-9))
    idx = np.flatnonzero(ok)
    if len(idx) < k:
        return None
    idx = idx[np.argsort(-pr[idx])][:k]
    return idx


def alloc(po_sel, pr_sel, mode, floor_mult=1.0):
    """配分。戻りは賭け金の配列（円）。組めなければ None。"""
    k = len(po_sel)
    n_units = C.BUDGET // C.UNIT
    if k > n_units:
        return None
    if mode == "equal":
        w = np.ones(k)
    elif mode == "dutch":
        w = 1.0 / po_sel
    else:                                   # conf
        floor_u = np.ceil(C.BUDGET * floor_mult / po_sel / C.UNIT).astype(int)
        floor_u = np.maximum(floor_u, 1)
        if floor_u.sum() > n_units:
            return None
        rest = n_units - floor_u.sum()
        p = np.maximum(pr_sel, 0)
        add = np.floor(rest * p / p.sum()).astype(int) if p.sum() > 0 else np.zeros(k, int)
        while add.sum() < rest:
            j = int(np.argmax(rest * p / max(p.sum(), 1e-12) - add))
            add[j] += 1
        return (floor_u + add) * C.UNIT
    u = np.ones(k, int)
    rest = n_units - k
    u += np.floor(rest * w / w.sum()).astype(int)
    while u.sum() < n_units:
        j = int(np.argmin(u / np.maximum(w, 1e-12)))
        u[j] += 1
    return u * C.UNIT


def run(type_label, window, kind, min_odds, k, mode, floor_mult=1.0):
    z = cache()
    idx = C.select(type_label, window)
    nd = len(set(z["DATE"][idx]))
    inv = pay = 0.0
    hits, ratios, means = [], [], []
    n = 0
    for i in idx:
        sel = pick(i, kind, min_odds, k)
        if sel is None:
            continue
        if kind == "trio":
            po = z["TRIO_PO"][i][sel]; pr = z["TRIO_PROB"][i][sel]
            win = int(z["TRIO_WIN"][i]); od = z["TRIO_ODDS"][i]
        else:
            po = z["PO"][i][sel]; pr = z["PROB"][i][sel]
            win = int(z["WIN"][i]); od = None
        st = alloc(po, pr, mode, floor_mult)
        if st is None:
            continue
        n += 1
        iv = float(st.sum()); inv += iv
        means.append(float((st * po).mean()))
        hitpos = np.flatnonzero(sel == win)
        p = 0.0
        if len(hitpos):
            j = int(hitpos[0])
            p = float(st[j] * od[win]) if kind == "trio" else float(st[j] / 100.0 * z["PAY"][i])
        pay += p
        if p > 0:
            hits.append(p); ratios.append(p / iv)
    if not n:
        return None
    q = lambda a, x: sorted(a)[int(len(a) * x)] if a else 0
    gami = sum(1 for r in ratios if r < 1)
    return dict(perday=n / nd, n=n, hit=len(hits) / n * 100,
                gami=gami / max(len(hits), 1) * 100,
                shown=(len(hits) - gami) / n * 100,
                q25=q(hits, .25), med=median(hits) if hits else 0, q90=q(hits, .9),
                med_mean=median(means), two=sum(1 for r in ratios if r >= 2) / nd,
                big=sum(1 for h in hits if h >= 100_000) / nd,
                roi=pay / inv * 100)


HDR = ("  {:22s} {:>6s} {:>7s} {:>6s} {:>8s} {:>26s} {:>9s} {:>7s} {:>7s} {:>6s}"
       .format("腕", "件/日", "的中%", "ガミ%", "表示的中", "払戻 q25/中央/q90",
               "想定平均", "2倍+/日", "10万+/日", "ROI%"))


def show(lbl, s):
    if not s:
        print(f"  {lbl:22s} (該当なし)"); return
    print(f"  {lbl:22s} {s['perday']:6.2f} {s['hit']:7.2f} {s['gami']:6.2f} {s['shown']:8.2f}"
          f" {s['q25']:7,.0f}/{s['med']:8,.0f}/{s['q90']:8,.0f} {s['med_mean']:9,.0f}"
          f" {s['two']:7.2f} {s['big']:7.3f} {s['roi']:6.1f}")


PLAN = {"A": ("tf", 0), "B": ("tf", 0), "C": ("tf", 20),
        "D": ("trio", 0), "E": ("tf", 30), "F": ("trio", 10)}


def main_alloc():
    """① 配分方式の比較（ゲートなし・採用案の券種と帯・点数は各型の採用値）。"""
    KS = {"A": 5, "B": 4, "C": 12, "D": 4, "E": 14, "F": 4}
    for t in "ABCDEF":
        kind, mo = PLAN[t]
        k = KS[t]
        print(f"\n=== 型{t}  {kind} / 帯 {mo}倍+ / {k}点  【ゲートなし】===")
        for w, wl in (("explore", "探索"), ("confirm", "確認(本番相当)")):
            print(f"[{wl}]"); print(HDR)
            for mode, fm, lbl in (("dutch", 1.0, "ダッチ"), ("equal", 1.0, "均等"),
                                  ("conf", 1.0, "信頼度傾斜 床1.0"),
                                  ("conf", 1.3, "信頼度傾斜 床1.3"),
                                  ("conf", 1.6, "信頼度傾斜 床1.6"),
                                  ("conf", 2.0, "信頼度傾斜 床2.0")):
                show(lbl, run(t, w, kind, mo, k, mode, fm))


def main_k():
    """② 点数の掃引（信頼度傾斜 床1.3・確認窓）。"""
    for t in "ABCDEF":
        kind, mo = PLAN[t]
        print(f"\n=== 型{t}  {kind} / 帯 {mo}倍+ / 点数掃引（信頼度傾斜 床1.3・確認窓）===")
        print(HDR)
        for k in (1, 2, 3, 4, 5, 6, 8, 10, 12, 14, 18, 24):
            show(f"{k}点", run(t, "confirm", kind, mo, k, "conf", 1.3))


def main_band():
    """③ 帯の掃引（信頼度傾斜 床1.3・点数は型の採用値・確認窓）。"""
    KS = {"A": 5, "B": 4, "C": 12, "D": 4, "E": 14, "F": 4}
    for t in "ABCDEF":
        kind, _ = PLAN[t]
        print(f"\n=== 型{t}  {kind} / {KS[t]}点 / 帯の掃引（信頼度傾斜 床1.3・確認窓）===")
        print(HDR)
        for mo in (0, 5, 10, 20, 30, 50, 100):
            show(f"予測{mo}倍+", run(t, "confirm", kind, mo, KS[t], "conf", 1.3))


if __name__ == "__main__":
    {"alloc": main_alloc, "k": main_k, "band": main_band}[sys.argv[1]]()
