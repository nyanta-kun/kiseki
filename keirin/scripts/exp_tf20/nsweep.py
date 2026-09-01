#!/usr/bin/env python3
"""1日の購入レース数 N を動かすと日次の見え方はどうなるか（2026-08-25）。

ユーザー提案の核心は「**20レースに絞る**」。ROI が壁に張り付く市場では
件数を動かしても収支は動かないが、**日次の分布（100%超の日・0円の日）は動く**。
どこが最良かを実測する。
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np

Z = np.load("/tmp/tf20_board.npz", allow_pickle=True)
PROB, PO = Z["PROB"].astype(np.float64), Z["PO"].astype(np.float64)
WIN, PAY = Z["WIN"], Z["PAY"]
DATE, DAYIDX, RTYPE = Z["DATE"].astype(str), Z["DAYIDX"], Z["RTYPE"].astype(str)
ok = WIN >= 0
PROB, PO, WIN, PAY, DATE, DAYIDX, RTYPE = (
    a[ok] for a in (PROB, PO, WIN, PAY, DATE, DAYIDX, RTYPE))
EXP = DATE < "2026-01-01"


def build(lo, hi, k, order):
    band = (PO >= lo) & (PO < hi)
    sc = np.where(band, PROB * PO if order == "ev" else PROB, -1.0)
    top = np.argsort(-sc, axis=1)[:, :k]
    valid = np.take_along_axis(band, top, 1)
    hit = ((top == WIN[:, None]) & valid).any(1)
    npt = valid.sum(1)
    st = np.array([max(100, (10000 // max(n, 1)) // 100 * 100) for n in npt])
    bet = np.where(npt > 0, st * npt, 0)
    pay = np.where(hit, PAY * st / 100.0, 0.0)
    # 選別スコア: 買った k 点の合計確率（= モデル自身の的中確率）
    cap = np.where(valid, np.take_along_axis(PROB, top, 1), 0).sum(1)
    ev = np.where(valid, np.take_along_axis(PROB * PO, top, 1), 0).sum(1)
    return hit, npt, bet, pay, cap, ev


def daily(mask, N, score, hit, npt, bet, pay, desc=True):
    d = defaultdict(list)
    for i in np.flatnonzero(mask & (npt > 0)):
        d[DATE[i]].append(i)
    out = []
    for dt, idx in d.items():
        idx = sorted(idx, key=lambda i: -score[i] if desc else score[i])[:N]
        b = sum(bet[i] for i in idx); p = sum(pay[i] for i in idx)
        h = sum(int(hit[i]) for i in idx)
        big = sum(1 for i in idx if pay[i] >= 100000)
        out.append((len(idx), b, p, h, big))
    a = np.array(out, float)
    return dict(days=len(a), per_day=a[:, 0].mean(), hits=a[:, 3].mean(),
                roi=a[:, 2].sum() / max(a[:, 1].sum(), 1),
                p100=float((a[:, 2] >= a[:, 1]).mean()),
                p150=float((a[:, 2] >= a[:, 1] * 1.5).mean()),
                zero=float((a[:, 2] == 0).mean()),
                big10=a[:, 4].mean(),
                inv=a[:, 1].mean())


for lo, hi, k, order in [(30, 1e9, 3, "prob"), (30, 1e9, 5, "prob"),
                         (50, 150, 3, "ev"), (10, 1e9, 5, "prob")]:
    hit, npt, bet, pay, cap, ev = build(lo, hi, k, order)
    ttl = f"{lo:g}{'倍+' if hi > 1e8 else f'-{hi:g}倍'} {k}点({order})"
    print(f"\n【{ttl}】的中{hit[npt>0].mean():.2%} / "
          f"払戻中央{np.median(pay[hit&(npt>0)]):,.0f}円")
    print(f"{'選別':>10}{'N':>4}{'期':>5}{'件/日':>7}{'投資/日':>10}{'本/日':>7}"
          f"{'ROI':>8}{'100%超':>8}{'150%超':>8}{'0円の日':>8}{'10万+/日':>9}")
    for sel, sc, desc in (("捕捉確率降順", cap, True), ("捕捉確率昇順", cap, False),
                          ("期待値降順", ev, True)):
        for N in (5, 10, 20, 30, 999):
            for per, m in (("探索", EXP), ("確認", ~EXP)):
                r = daily(m, N, sc, hit, npt, bet, pay, desc)
                lab = f"{sel}" if (N == 5 and per == "探索") else ""
                print(f"{lab:>10}{N if N < 999 else '全':>4}{per:>5}"
                      f"{r['per_day']:>7.1f}{r['inv']:>10,.0f}{r['hits']:>7.2f}"
                      f"{r['roi']:>8.1%}{r['p100']:>8.1%}{r['p150']:>8.1%}"
                      f"{r['zero']:>8.1%}{r['big10']:>9.2f}")
        print()
