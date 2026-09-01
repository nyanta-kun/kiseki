#!/usr/bin/env python3
"""運用案の最終比較（2026-08-26）。"""
from __future__ import annotations
import sys
import numpy as np
sys.path.insert(0, "scripts/exp_honmei_tf")
from scan import PROB, PO, WIN, PAY, DATE, RTYPE, OK, EXP, CNF, POSMASK
from frame import buy

KESSHO = np.isin(RTYPE, ["決勝", "チャレンジ決勝"])
WIDE = np.isin(RTYPE, ["予選", "準決勝", "特一般"])
WIDE2 = np.isin(RTYPE, ["予選", "準決勝", "特一般", "決勝", "特選"])
Q = np.array([f"{d[:4]}Q{(int(d[5:7])-1)//3+1}" for d in DATE])
QS = sorted(set(Q[OK]))

def show(label, sub, lo, k):
    n, inv, ret, hit = buy(POSMASK["any"] & (PO >= lo), PROB, k)
    m = sub & OK & (n > 0)
    days = sorted(set(DATE[m]))
    rng = np.random.default_rng(0); idx = np.flatnonzero(m)
    bs = [ret[s].sum()/inv[s].sum()*100 for s in (rng.choice(idx, len(idx)) for _ in range(400))]
    ci = np.percentile(bs, [2.5, 97.5])
    dbig = np.array([((ret >= 100000) & m & (DATE == d)).sum() for d in days])
    dhit = np.array([hit[m & (DATE == d)].sum() for d in days])
    qroi = [ret[m & (Q == q)].sum()/inv[m & (Q == q)].sum()*100 for q in QS
            if (m & (Q == q)).sum() >= 50]
    print(f"{label:<30} {m.sum()/len(days):5.2f}件/日 的中{hit[m].mean()*100:5.2f}% "
          f"ROI{ret[m].sum()/inv[m].sum()*100:6.1f}% CI[{ci[0]:5.1f},{ci[1]:5.1f}] "
          f"中央{np.median(ret[m][hit[m]]):>8,.0f}円 "
          f"10万+{dbig.sum()/len(days):5.2f}件/日({np.mean(dbig>0):4.1%}の日) "
          f"的中0の日{np.mean(dhit==0):5.1%} 投資{np.mean([inv[m&(DATE==d)].sum() for d in days]):>8,.0f}円/日 "
          f"四半期{min(qroi):5.1f}〜{max(qroi):5.1f}%")

print("=" * 210)
print("■ 運用案の比較（全期間 2024-07〜2026-08・vintage walk-forward）")
print("=" * 210)
show("A 現行7T3: 決勝系30倍+5点", KESSHO, 30, 5)
show("B 決勝系50倍+5点", KESSHO, 50, 5)
show("B' 決勝系50倍+8点", KESSHO, 50, 8)
show("C 予選/準決/特一般 50倍+5点", WIDE, 50, 5)
show("C' 予選/準決/特一般 30倍+5点", WIDE, 30, 5)
show("D 上記+決勝+特選 50倍+5点", WIDE2, 50, 5)
show("参考 全レース50倍+5点", np.ones(len(WIN), bool), 50, 5)
print()
print("■ 確認窓(2026-01〜)のみで同じ比較")
print("=" * 210)
_o = globals()
for lbl, sub, lo, k in (("A 現行7T3", KESSHO, 30, 5), ("B 決勝系50倍+", KESSHO, 50, 5),
                        ("C 予選/準決/特一般50倍+", WIDE, 50, 5), ("D 5種別50倍+", WIDE2, 50, 5)):
    show(lbl + " [確認窓]", sub & CNF, lo, k)
