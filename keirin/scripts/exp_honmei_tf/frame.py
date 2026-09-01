#!/usr/bin/env python3
"""50倍帯の運用形: 混戦度選別・配分・日次の当たり方（2026-08-26）。"""
from __future__ import annotations
import sys
import numpy as np
sys.path.insert(0, "scripts/exp_honmei_tf")
from scan import (PROB, PO, WIN, PAY, DATE, RTYPE, P3, PW, CANON, H, OK, EXP, CNF,
                  POSMASK, BUDGET, UNIT)

KESSHO = np.isin(RTYPE, ["決勝", "チャレンジ決勝"])
CONC = np.sort(PROB, 1)[:, ::-1][:, :5].sum(1)      # 上位5点確率和（低い=混戦）


def buy(mask, key, n_legs, tilt=False):
    sc = np.where(mask, key, -np.inf)
    order = np.argsort(-sc, axis=1)[:, :n_legs]
    valid = np.take_along_axis(sc, order, 1) > -np.inf
    n = valid.sum(1)
    if not tilt:
        st = np.where(n > 0, (BUDGET // np.maximum(n, 1)) // UNIT * UNIT, 0)
        stake = np.where(valid, st[:, None], 0)
    else:                                   # 予測オッズの逆数比（払戻を揃える）
        o = np.take_along_axis(PO, order, 1)
        w = np.where(valid, 1.0 / np.maximum(o, 1e-9), 0.0)
        w = w / np.maximum(w.sum(1, keepdims=True), 1e-12)
        stake = np.floor(w * BUDGET / UNIT) * UNIT
    inv = stake.sum(1)
    hitm = (order == WIN[:, None]) & valid
    ret = (np.where(hitm, PAY[:, None] * stake / 100.0, 0.0)).sum(1)
    return n, inv, ret, hitm.any(1)


def rep(label, sub, n, inv, ret, hit):
    cells = []
    for wn, w in (("探索", EXP), ("確認", CNF)):
        m = sub & w & (n > 0)
        if m.sum() < 30:
            cells.append(f"{wn}: n={m.sum()}"); continue
        days = len(set(DATE[m]))
        roi = ret[m].sum() / inv[m].sum() * 100
        med = np.median(ret[m][hit[m]]) if hit[m].any() else 0
        gami = ((ret <= inv) & hit & m).sum() / max(hit[m].sum(), 1) * 100
        cells.append(f"{wn}: {m.sum()/days:5.2f}件/日 的中{hit[m].mean()*100:5.2f}% "
                     f"ROI{roi:6.1f}% 中央{med:8,.0f}円 ガミ{gami:4.1f}% "
                     f"10万+{((ret>=100000)&m).sum()/days:5.3f}件/日")
    print(f"{label:<34} " + "  ".join(cells))


ALL = np.ones(len(WIN), bool)
print("=" * 158)
print("■ A. 混戦度で絞る（上位5点確率和が低い＝荒れやすい / 50倍+ 確率順5点・全レース）")
print("=" * 158)
n, inv, ret, hit = buy(POSMASK["any"] & (PO >= 50), PROB, 5)
rep("全レース", ALL, n, inv, ret, hit)
for q in (10, 25, 50):
    th = np.percentile(CONC[OK], q)
    rep(f"混戦 下位{q}%（荒れ側）", CONC <= th, n, inv, ret, hit)
    th2 = np.percentile(CONC[OK], 100 - q)
    rep(f"堅い 上位{q}%", CONC >= th2, n, inv, ret, hit)

print("\n" + "=" * 158)
print("■ B. 配分（均等 ⇄ 予測オッズ逆数のダッチング）")
print("=" * 158)
for lo, k in ((30, 5), (50, 5), (50, 10)):
    for tl, nm in ((False, "均等"), (True, "ダッチ")):
        n, inv, ret, hit = buy(POSMASK["any"] & (PO >= lo), PROB, k, tilt=tl)
        rep(f"{lo}倍+ {k}点 {nm} 全レース", ALL, n, inv, ret, hit)
        rep(f"{lo}倍+ {k}点 {nm} 決勝系", KESSHO, n, inv, ret, hit)

print("\n" + "=" * 158)
print("■ C. 日次の当たり方（確認窓 2026-01〜08）")
print("=" * 158)
for lbl, sub, lo, k in (("全レース 30倍+5点", ALL, 30, 5), ("全レース 50倍+5点", ALL, 50, 5),
                        ("全レース 50倍+10点", ALL, 50, 10), ("決勝系 30倍+5点(≒7T3)", KESSHO, 30, 5),
                        ("決勝系 50倍+5点", KESSHO, 50, 5)):
    n, inv, ret, hit = buy(POSMASK["any"] & (PO >= lo), PROB, k)
    m = sub & CNF & (n > 0)
    days = sorted(set(DATE[m]))
    dh = np.array([hit[m & (DATE == d)].sum() for d in days])
    dr = np.array([ret[m & (DATE == d)].sum() for d in days])
    di = np.array([inv[m & (DATE == d)].sum() for d in days])
    big = np.array([((ret >= 100000) & m & (DATE == d)).sum() for d in days])
    print(f"{lbl:<22} {len(days)}日  的中0件の日 {np.mean(dh==0):5.1%}  "
          f"日次ROI100%超 {np.mean(dr>=di):5.1%}  10万+が出た日 {np.mean(big>0):5.1%}  "
          f"1日投資 {di.mean():>9,.0f}円")
