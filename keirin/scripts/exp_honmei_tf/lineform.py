#!/usr/bin/env python3
"""「指数1位 + 別ライン2車」を買い目にするとどうなるか（2026-08-26）。"""
from __future__ import annotations
import itertools, sys
import numpy as np

z = np.load("/tmp/honmei_attr.npz", allow_pickle=True)
PROB, PO, WIN, PAY = (z["PROB"].astype(np.float64), z["PO"].astype(np.float64), z["WIN"], z["PAY"])
DATE, RTYPE, P3, LG = z["DATE"], z["RTYPE"], z["P3"], z["LG"]
LSZ, NL = z["A_line_size"], z["A_n_lines"]
CANON = np.array(list(itertools.permutations(range(1, 8), 3)))
N = len(WIN); OK = WIN >= 0
EXP = (DATE < "2026-01-01") & OK; CNF = (DATE >= "2026-01-01") & OK
Q = np.array([f"{d[:4]}Q{(int(d[5:7])-1)//3+1}" for d in DATE]); QS = sorted(set(Q[OK]))
BUDGET, UNIT = 10_000, 100
r = np.arange(N); H = P3.argmax(1) + 1; hi = H - 1

# 各買い目(210)について: 指数1位を含むか / 指数1位と別ラインの車が何車か
HASH = (CANON[None, :, :] == H[:, None, None]).any(2)                 # (N,210)
lg_of = LG[r[:, None, None], CANON[None] - 1]                          # (N,210,3)
same = (lg_of == LG[r, hi][:, None, None])                             # 指数1位と同ライン
n_same_other = (same & (CANON[None] != H[:, None, None])).sum(2)       # 相手のうち同ライン数
OTHER2 = HASH & (n_same_other == 0)      # 指数1位 + 別ライン2車
OTHER1 = HASH & (n_same_other == 1)
SAME2 = HASH & (n_same_other == 2)

def buy(mask, key, k, extra=None):
    m = mask if extra is None else (mask & extra)
    sc = np.where(m, key, -np.inf)
    o = np.argsort(-sc, 1)[:, :k]
    v = np.take_along_axis(sc, o, 1) > -np.inf
    n = v.sum(1)
    st = np.where(n > 0, (BUDGET // np.maximum(n, 1)) // UNIT * UNIT, 0)
    inv = st * n
    hit = ((o == WIN[:, None]) & v).any(1)
    ret = np.where(hit, PAY * st / 100.0, 0.0)
    return n, inv, ret, hit

def rep(label, sub, n, inv, ret, hit):
    cells = []
    for wn, w in (("探索", EXP), ("確認", CNF)):
        m = sub & w & (n > 0)
        if m.sum() < 100:
            cells.append(f"{wn}: n={m.sum()}"); continue
        days = len(set(DATE[m]))
        med = np.median(ret[m][hit[m]]) if hit[m].any() else 0
        cells.append(f"{wn} {m.sum()/days:5.2f}件/日 的中{hit[m].mean()*100:5.2f}% "
                     f"ROI{ret[m].sum()/inv[m].sum()*100:6.1f}% 中央{med:>8,.0f}円 "
                     f"10万+{((ret>=100000)&m).sum()/days:5.2f}件/日")
    print(f"{label:<38} " + " | ".join(cells))

ALL = np.ones(N, bool)
print("=" * 168)
print("■ 1. 現行の買い方（確率上位N点）は、相手のライン構成をどう選んでいるか")
print("=" * 168)
for lo, k in ((1, 5), (30, 5), (50, 5), (50, 10), (100, 5)):
    sc = np.where(PO >= lo, PROB, -np.inf)
    o = np.argsort(-sc, 1)[:, :k]
    a = np.arange(N)[:, None]
    s2 = SAME2[a, o].mean(); s1 = OTHER1[a, o].mean(); s0 = OTHER2[a, o].mean()
    nh = (~HASH)[a, o].mean()
    print(f"{lo:>4}倍+ {k:>2}点: 指数1位のライン3車 {s2*100:5.1f}%  1車が同ライン {s1*100:5.1f}%  "
          f"別ライン2車 {s0*100:5.1f}%  指数1位を含まない {nh*100:5.1f}%")
print("  （参考）実際の決着: 50倍未満は 同ライン2車 36%/別ライン2車 17%、100倍+ は 4%/51%")

print("\n" + "=" * 168)
print("■ 2.「指数1位＋別ライン2車」に限定して買う（確率順・均等・1万円）")
print("=" * 168)
for lo in (1, 30, 50):
    for k in (5, 10):
        rep(f"制限なし {lo}倍+ {k}点", ALL, *buy(HASH, PROB, k, PO >= lo))
        rep(f"→ 別ライン2車限定 {lo}倍+ {k}点", ALL, *buy(OTHER2, PROB, k, PO >= lo))
    print("-" * 168)

print("\n" + "=" * 168)
print("■ 3. 四半期別の安定性（別ライン2車限定・壁 74.85%）")
print("=" * 168)
KESSHO = np.isin(RTYPE, ["決勝", "チャレンジ決勝"])
WIDE = np.isin(RTYPE, ["予選", "準決勝", "特一般"])
for lbl, sub, mk_, lo, k in (
        ("全レース 別2 50倍+ 10点", ALL, OTHER2, 50, 10),
        ("全レース 別2 30倍+ 10点", ALL, OTHER2, 30, 10),
        ("全レース 別2 制限なし 10点", ALL, OTHER2, 1, 10),
        ("予選/準決/特一般 別2 50倍+10点", WIDE, OTHER2, 50, 10),
        ("4-5ライン 別2 50倍+ 10点", NL[r, hi] >= 4, OTHER2, 50, 10),
        ("参考 制限なし 50倍+ 10点", ALL, HASH, 50, 10)):
    n, inv, ret, hit = buy(mk_, PROB, k, PO >= lo)
    m = sub & OK & (n > 0)
    days = len(set(DATE[m]))
    rng = np.random.default_rng(0); idx = np.flatnonzero(m)
    bs = [ret[s].sum()/inv[s].sum()*100 for s in (rng.choice(idx, len(idx)) for _ in range(400))]
    ci = np.percentile(bs, [2.5, 97.5])
    qs, nw, nq = [], 0, 0
    for q in QS:
        mq = m & (Q == q)
        if mq.sum() < 40: qs.append("    -"); continue
        v = ret[mq].sum()/inv[mq].sum()*100; qs.append(f"{v:5.0f}"); nq += 1; nw += v > 74.85
    print(f"{label if False else lbl:<28}{m.sum()/days:5.2f}件/日 的中{hit[m].mean()*100:5.2f}% "
          f"ROI{ret[m].sum()/inv[m].sum()*100:6.1f}% CI[{ci[0]:5.1f},{ci[1]:5.1f}] 壁超{nw}/{nq}  "
          + " ".join(qs))
