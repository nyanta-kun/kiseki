#!/usr/bin/env python3
"""混戦選別 × 帯 × 点数 の安定性確認（2026-08-26）。"""
from __future__ import annotations
import sys
import numpy as np
sys.path.insert(0, "scripts/exp_honmei_tf")
from scan import PROB, PO, WIN, PAY, DATE, RTYPE, P3, CANON, OK, EXP, CNF, POSMASK
from frame import buy

N = len(WIN)
TOP3 = CANON[np.clip(WIN, 0, None)]
RANKP3 = np.argsort(np.argsort(-P3, 1), 1) + 1
FIN_R = RANKP3[np.arange(N)[:, None], TOP3 - 1]
E = (FIN_R == 1).any(1) & (PAY >= 5000) & OK
CONC = np.sort(PROB, 1)[:, ::-1][:, :5].sum(1)
Q = np.array([f"{d[:4]}Q{(int(d[5:7])-1)//3+1}" for d in DATE])
QS = sorted(set(Q[OK]))
KESSHO = np.isin(RTYPE, ["決勝", "チャレンジ決勝"])
WIDE = np.isin(RTYPE, ["予選", "準決勝", "特一般"])

def line(label, sub, lo, k):
    n, inv, ret, hit = buy(POSMASK["any"] & (PO >= lo), PROB, k)
    m = sub & OK & (n > 0)
    if m.sum() < 200:
        print(f"{label:<32} n={m.sum()} 少なすぎ"); return
    days = len(set(DATE[m]))
    rng = np.random.default_rng(0); idx = np.flatnonzero(m)
    bs = [ret[s].sum()/inv[s].sum()*100 for s in (rng.choice(idx, len(idx)) for _ in range(400))]
    ci = np.percentile(bs, [2.5, 97.5])
    qs = []
    for q in QS:
        mq = m & (Q == q)
        qs.append(f"{ret[mq].sum()/inv[mq].sum()*100:5.0f}" if mq.sum() >= 40 else "    -")
    nwall = sum(1 for q in QS if (m & (Q == q)).sum() >= 40
                and ret[m & (Q == q)].sum()/inv[m & (Q == q)].sum()*100 > 74.85)
    nq = sum(1 for q in QS if (m & (Q == q)).sum() >= 40)
    print(f"{label:<32}{m.sum()/days:5.2f}件/日 的中{hit[m].mean()*100:5.2f}% "
          f"ROI{ret[m].sum()/inv[m].sum()*100:6.1f}% CI[{ci[0]:5.1f},{ci[1]:5.1f}] "
          f"壁超{nwall}/{nq}窓  " + " ".join(qs))

TH = np.percentile(CONC[OK], 10)
TH20 = np.percentile(CONC[OK], 20)
print("=" * 178)
print(f"■ 四半期別 ROI（{' '.join(QS)}）  混戦上位10% ⇔ CONC <= {TH:.4f}")
print("=" * 178)
for lo, k in ((50, 5), (50, 10), (50, 15), (30, 10), (100, 5)):
    line(f"全レース {lo}倍+ {k}点", np.ones(N, bool), lo, k)
    line(f"  └混戦上位10% {lo}倍+ {k}点", CONC <= TH, lo, k)
    line(f"  └混戦上位20% {lo}倍+ {k}点", CONC <= TH20, lo, k)
print()
line("予選/準決/特一般 50倍+ 10点", WIDE, 50, 10)
line("  └×混戦上位20%", WIDE & (CONC <= TH20), 50, 10)
line("決勝系 30倍+ 5点（現行7T3）", KESSHO, 30, 5)
