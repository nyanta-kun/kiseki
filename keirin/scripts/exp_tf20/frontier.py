#!/usr/bin/env python3
"""三連単「1日20レース・1レース10万回収・2〜3本で日次100%超」は成り立つか。

ユーザー提案:
> 三連単でレース単位での回収を10万に設定。1日の購入レースを20レースくらいに絞り
> 2〜3本当たったらその日は100%超えを狙う買い目

算数: 20R × 1万 = 20万/日。2本×10万 = 20万（＝ちょうど100%）、3本で150%。
      **これは「持続 ROI 100〜125%」の商品**を意味する。払戻率の壁は 74.85%。
      成り立つかを帯・点数・選別・件数の全面掃引で確かめる。

板: /tmp/tf20_board.npz（vintage 予測・本番 predict_board）
⚠️ 予測オッズモデルの train_end は 2025-12-31。**2026 窓だけが真の確認窓**。
"""
from __future__ import annotations

import sys
from collections import defaultdict

import numpy as np

Z = np.load("/tmp/tf20_board.npz", allow_pickle=True)
PROB, PO = Z["PROB"].astype(np.float64), Z["PO"].astype(np.float64)
WIN, PAY = Z["WIN"], Z["PAY"]
DATE, DAYIDX, RTYPE = Z["DATE"].astype(str), Z["DAYIDX"], Z["RTYPE"].astype(str)
GRADE, VENUE, KEY = Z["GRADE"].astype(str), Z["VENUE"].astype(str), Z["KEY"].astype(str)
ok = WIN >= 0
PROB, PO, WIN, PAY, DATE, DAYIDX, RTYPE, GRADE, VENUE, KEY = (
    a[ok] for a in (PROB, PO, WIN, PAY, DATE, DAYIDX, RTYPE, GRADE, VENUE, KEY))
EXP = DATE < "2026-01-01"
N = len(PROB)
print(f"板 {N:,}R  探索(〜2025-12) {EXP.sum():,} / 確認(2026-) {(~EXP).sum():,}")
print(f"日数 探索 {len(set(DATE[EXP])):,} / 確認 {len(set(DATE[~EXP])):,}\n")


def stake_of(k):
    return max(100, (10000 // k) // 100 * 100)


def pick(lo, hi, k, order="prob"):
    """予測オッズ帯 [lo,hi) の中から k 点。order: prob=確率順 / ev=期待値順。"""
    band = (PO >= lo) & (PO < hi)
    sc = PROB * PO if order == "ev" else PROB
    sc = np.where(band, sc, -1.0)
    top = np.argsort(-sc, axis=1)[:, :k]
    valid = np.take_along_axis(band, top, 1)
    hit = ((top == WIN[:, None]) & valid).any(1)
    npt = valid.sum(1)
    return hit, npt, top, valid


def day_stats(mask, hit, npt, label, sel_per_day=None):
    """日次に畳む。sel_per_day=N ならその日の上位N件だけ買う（選別済み前提）。"""
    st = np.array([stake_of(max(k, 1)) for k in npt])
    bet = np.where(npt > 0, st * npt, 0)
    payv = np.where(hit, PAY * st / 100.0, 0.0)
    d = defaultdict(lambda: [0.0, 0.0, 0, 0])
    for i in np.flatnonzero(mask & (npt > 0)):
        z = d[DATE[i]]
        z[0] += bet[i]; z[1] += payv[i]; z[2] += int(hit[i]); z[3] += 1
    if not d:
        return None
    v = np.array(list(d.values()))
    tot_bet, tot_pay = v[:, 0].sum(), v[:, 1].sum()
    m = mask & (npt > 0)
    hp = payv[m & hit]
    return dict(label=label, R=int(m.sum()), days=len(d),
                per_day=v[:, 3].mean(), hit=hit[m].mean(),
                hits_per_day=v[:, 2].mean(),
                roi=tot_pay / max(tot_bet, 1),
                med=float(np.median(hp)) if len(hp) else 0.0,
                p100=float((v[:, 1] >= v[:, 0]).mean()),
                zero=float((v[:, 1] == 0).mean()),
                big10=float((payv[m] >= 100000).sum() / len(d)),
                big30=float((payv[m] >= 300000).sum() / len(d)))


def show(rows, title):
    print(f"\n【{title}】")
    print(f"{'構成':<26}{'期':>5}{'件/日':>7}{'的中%':>7}{'本/日':>7}{'ROI':>8}"
          f"{'払戻中央':>10}{'100%超の日':>10}{'0円の日':>8}{'10万+/日':>9}")
    for r in rows:
        if r is None:
            continue
        print(f"{r['label']:<26}{r['期']:>5}{r['per_day']:>7.1f}{r['hit']:>7.2%}"
              f"{r['hits_per_day']:>7.2f}{r['roi']:>8.1%}{r['med']:>10,.0f}"
              f"{r['p100']:>10.1%}{r['zero']:>8.1%}{r['big10']:>9.2f}")


# ═══ 1. 帯 × 点数（全レース・選別なし）═══
rows = []
for lo, hi in [(1, 1e9), (10, 1e9), (30, 1e9), (50, 1e9), (100, 1e9),
               (30, 100), (50, 150), (100, 300)]:
    for k in (3, 5, 10):
        order = "ev" if lo >= 50 else "prob"
        hit, npt, _, _ = pick(lo, hi, k, order)
        lab = f"{lo:g}{'倍+' if hi > 1e8 else f'-{hi:g}倍'} {k}点({order})"
        for per, m in (("探索", EXP), ("確認", ~EXP)):
            r = day_stats(m, hit, npt, lab)
            if r:
                r["期"] = per; rows.append(r)
show(rows, "帯 × 点数（全レース・選別なし）")

# ═══ 2. 「1レース10万回収」に必要な条件 ═══
print("\n【1レース10万円の払戻に必要な確定オッズ】1レース1万円")
for k in (3, 5, 8, 10):
    st = stake_of(k)
    print(f"  {k:>2}点 → 1点{st:,}円 → 10万には確定 {100000/st*100/100:>5.0f}倍以上が要る"
          f"  （実際に的中買い目がその倍率以上のレースは "
          f"{(PAY >= 100000/st*100).mean():.1%}）")
