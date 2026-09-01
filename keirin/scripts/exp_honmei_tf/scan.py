#!/usr/bin/env python3
"""本命絡み三連単: 買い方のグリッドスキャン（2026-08-26）。

本命(◎ = p3 1位)を必ず含む三連単を、位置・オッズ帯・点数・並べ替え規則で
掃引し、探索窓(2024-07〜2025-12)/確認窓(2026-01〜)で評価する。

⚠️ 予測オッズ `odds_tf_n7` は train_end 2025-12-31 → 探索窓は in-sample。
"""
from __future__ import annotations
import itertools, sys
import numpy as np

BUDGET, UNIT = 10_000, 100
z = np.load("/tmp/honmei_tf.npz", allow_pickle=True)
PROB, PO, WIN, PAY = z["PROB"].astype(np.float64), z["PO"].astype(np.float64), z["WIN"], z["PAY"]
DATE, RTYPE, P3, PW = z["DATE"], z["RTYPE"], z["P3"], z["PW"]
CANON = np.array(list(itertools.permutations(range(1, 8), 3)))
H = P3.argmax(1) + 1
OK = WIN >= 0
EXP = (DATE < "2026-01-01") & OK
CNF = (DATE >= "2026-01-01") & OK

POSMASK = {p: (CANON[None, :, p - 1] == H[:, None]) for p in (1, 2, 3)}
POSMASK["any"] = POSMASK[1] | POSMASK[2] | POSMASK[3]

EV = PROB * PO


def evaluate(mask, key, n_legs, w=None):
    """mask(N,210) の中から key(N,210) の降順に n_legs 点。均等配分。"""
    sc = np.where(mask, key, -np.inf)
    order = np.argsort(-sc, axis=1)[:, :n_legs]
    valid = np.take_along_axis(sc, order, 1) > -np.inf
    n = valid.sum(1)
    stake = np.where(n > 0, (BUDGET // np.maximum(n, 1)) // UNIT * UNIT, 0)
    hit = (order == WIN[:, None]) & valid
    hit_any = hit.any(1)
    ret = np.where(hit_any, PAY * stake / 100.0, 0.0)
    inv = stake * n
    return n, inv, ret, hit_any


def report(label, sub, n, inv, ret, hit):
    out = []
    for wn, w in (("探索", EXP), ("確認", CNF)):
        m = sub & w & (n > 0)
        if m.sum() == 0:
            out.append(f"{wn}: -")
            continue
        days = len(set(DATE[m]))
        roi = ret[m].sum() / inv[m].sum() * 100
        h = hit[m]
        disp = ((ret > inv) & m).sum() / m.sum() * 100      # ガミ除き = 表示的中
        p = ret[m][h[:0] if False else hit[m]]
        med = np.median(ret[m][hit[m]]) if hit[m].any() else 0
        out.append(f"{wn}: {m.sum()/days:5.2f}件/日 的中{h.mean()*100:5.2f}% "
                   f"表示{disp:5.2f}% ROI{roi:6.1f}% 中央{med:8,.0f}円 "
                   f"5万+{((ret>=50000)&m).sum():4d}件")
    print(f"{label:<44} " + "  ".join(out))


if __name__ == "__main__":
    ALL = np.ones(len(WIN), bool)
    print("=" * 150)
    print("■ 本命の位置 × オッズ下限 × 点数（PL確率の高い順に買う・全レース・均等配分）")
    print("=" * 150)
    for pos in (1, 2, 3, "any"):
        for lo in (1, 30, 50, 100):
            for k in (5, 8):
                mask = POSMASK[pos] & (PO >= lo)
                n, inv, ret, hit = evaluate(mask, PROB, k)
                report(f"本命{pos}着 / {lo:>3}倍+ / {k}点 / 確率順", ALL, n, inv, ret, hit)
        print("-" * 150)

    print("\n" + "=" * 150)
    print("■ 並べ替え規則の比較（本命どこでも・50倍+・5点）")
    print("=" * 150)
    for nm, key in (("確率順", PROB), ("EV順", EV), ("オッズ低い順", -PO)):
        n, inv, ret, hit = evaluate(POSMASK["any"] & (PO >= 50), key, 5)
        report(f"any / 50倍+ / 5点 / {nm}", ALL, n, inv, ret, hit)
