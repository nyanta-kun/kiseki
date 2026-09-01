#!/usr/bin/env python3
"""E =（確定50倍+ ∧ 指数1位3着内）は事前に選別できるか（2026-08-26）。"""
from __future__ import annotations
import sys
import numpy as np
sys.path.insert(0, "scripts/exp_honmei_tf")
from scan import PROB, PO, WIN, PAY, DATE, RTYPE, P3, PW, CANON, H, OK, EXP, CNF

N = len(WIN)
TOP3 = CANON[np.clip(WIN, 0, None)]
RANKP3 = np.argsort(np.argsort(-P3, 1), 1) + 1
FIN_R = RANKP3[np.arange(N)[:, None], TOP3 - 1]
IN3 = (FIN_R == 1).any(1) & OK
HI = (PAY >= 5000) & OK
E = IN3 & HI

p3n = P3 / P3.sum(1, keepdims=True)
pwn = PW / PW.sum(1, keepdims=True)
sp3 = np.sort(p3n, 1)[:, ::-1]
spw = np.sort(pwn, 1)[:, ::-1]
CONC = np.sort(PROB, 1)[:, ::-1][:, :5].sum(1)

FEAT = {
    "本命p3（高い＝来る）": P3.max(1),
    "本命pw": PW.max(1),
    "1着率エントロピー（高い＝混戦）": -(pwn * np.log(pwn + 1e-12)).sum(1),
    "p3エントロピー": -(p3n * np.log(p3n + 1e-12)).sum(1),
    "抜け度 gap12(pw)": spw[:, 0] - spw[:, 1],
    "p3 1位-2位差": sp3[:, 0] - sp3[:, 1],
    "p3 2位-3位差（2番手が抜けない）": -(sp3[:, 1] - sp3[:, 2]),
    "上位5点確率和 CONC（低い＝混戦）": -CONC,
    "p3 上位3車の和（低い＝相手が割れる）": -sp3[:, :3].sum(1),
    "予測板 100倍+点数": (PO >= 100).sum(1).astype(float),
}

def auc(s, y):
    o = np.argsort(s); y = y[o]
    r = np.arange(1, len(y) + 1); n1, n0 = y.sum(), (~y).sum()
    return (r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0) if n1 and n0 else np.nan

print("=" * 118)
print("■ 単一量で E を当てられるか（E率 25.6%）")
print("=" * 118)
print(f"{'量':<34}{'E の AUC':>18}{'50倍+ 単独':>16}{'指数1位3着内 単独':>20}")
for nm, f in FEAT.items():
    print(f"{nm:<34} 探索{auc(f[EXP],E[EXP]):.4f} 確認{auc(f[CNF],E[CNF]):.4f}"
          f"   {auc(f[EXP],HI[EXP]):.4f}/{auc(f[CNF],HI[CNF]):.4f}"
          f"   {auc(f[EXP],IN3[EXP]):.4f}/{auc(f[CNF],IN3[CNF]):.4f}")

print("\n" + "=" * 118)
print("■ 分位別の E 発生率（lift）— 最良候補で切る")
print("=" * 118)
for nm in ("本命p3（高い＝来る）", "1着率エントロピー（高い＝混戦）", "上位5点確率和 CONC（低い＝混戦）",
           "p3 2位-3位差（2番手が抜けない）"):
    f = FEAT[nm]
    print(f"--- {nm} ---")
    for wn, w in (("探索", EXP), ("確認", CNF)):
        q = np.percentile(f[w], [20, 40, 60, 80])
        b = np.digitize(f[w], q)
        cells = [f"Q{i+1} {E[w][b==i].mean():5.1%}(n={ (b==i).sum():,})" for i in range(5)]
        print(f"   {wn}: " + "  ".join(cells))

print("\n" + "=" * 118)
print("■ 67特徴級のモデルは要るか — 上の量を全部束ねた LightGBM（探索で学習→確認で評価）")
print("=" * 118)
try:
    import lightgbm as lgb
    X = np.column_stack([FEAT[k] for k in FEAT])
    tr, te = EXP, CNF
    m = lgb.train({"objective": "binary", "verbose": -1, "learning_rate": 0.05,
                   "num_leaves": 15, "min_data_in_leaf": 200, "seed": 0},
                  lgb.Dataset(X[tr], E[tr].astype(int)), num_boost_round=300)
    p = m.predict(X[te])
    print(f"   多変量 LGBM: 確認窓 AUC {auc(p, E[te]):.4f}   "
          f"（最良単一量 {max(auc(FEAT[k][te], E[te]) for k in FEAT):.4f}）")
    for q in (5, 10, 20, 30):
        th = np.percentile(p, 100 - q)
        print(f"     上位{q:>2}%を選ぶと E率 {E[te][p>=th].mean():5.1%}  "
              f"(ベース 25.6% / lift {E[te][p>=th].mean()/E[te].mean():.2f}x)  n={(p>=th).sum():,}")
except ImportError:
    print("   lightgbm 未導入")
