#!/usr/bin/env python3
"""第1〜2章 日次の的中数は二項分布より散らばっているか（2026-09-10）。

観測統計量は分散比
    phi = Σ_d (k_d − n_d r̄)² / Σ_d n_d r̄(1−r̄)
（k_d = その日の表示的中数・n_d = その日の採点済み件数・r̄ = 窓の全体率）。
独立ベルヌーイなら phi ≈ 1。

帰無分布は**並べ替え**で作る（分布の仮定を置かない）:
  A 全体シャッフル       … 構成も日内相関も壊す → 「日ごとの散らばりは二項か」
  B プラン内シャッフル   … 構成を保ち日内相関を壊す → 「構成を除いても散るか」
  C 日内シャッフル(会場) … 日は保ち会場のまとまりを壊す → 「同じ会場は同じ方向に外れるか」
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.exp_type_lab.split_common import (  # noqa: E402
    apply_cap, load_races, window)

ITERS = 2000


def phi(k, n, rbar):
    v = (n * rbar * (1 - rbar)).sum()
    return float(((k - n * rbar) ** 2).sum() / v) if v > 0 else float("nan")


def by_group(y, g):
    idx = defaultdict(list)
    for i, key in enumerate(g):
        idx[key].append(i)
    return {k: np.array(v) for k, v in idx.items()}


def perm_test(y, day, strata, seed=0, iters=ITERS):
    """strata 内でシャッフル（strata=None なら全体）。"""
    rng = np.random.default_rng(seed)
    dg = by_group(y, day)
    days = list(dg)
    n = np.array([len(dg[d]) for d in days], float)
    rbar = y.mean()
    k_obs = np.array([y[dg[d]].sum() for d in days], float)
    obs = phi(k_obs, n, rbar)
    blocks = [np.arange(len(y))] if strata is None else list(by_group(y, strata).values())
    sims = np.empty(iters)
    for t in range(iters):
        z = y.copy()
        for b in blocks:
            z[b] = rng.permutation(y[b])
        kk = np.array([z[dg[d]].sum() for d in days], float)
        sims[t] = phi(kk, n, rbar)
    p = (1 + (sims >= obs).sum()) / (iters + 1)
    return obs, float(np.median(sims)), float(np.percentile(sims, 97.5)), float(p)


def icc_pairs(y, keys):
    """同じ key に属する対の相関（残差の対積の平均 / 分散）。"""
    g = by_group(y, keys)
    r = y - y.mean()
    num = tot = 0.0
    for idx in g.values():
        if len(idx) < 2:
            continue
        s = r[idx].sum()
        num += (s * s - (r[idx] ** 2).sum()) / 2
        tot += len(idx) * (len(idx) - 1) / 2
    return num / tot / r.var() if tot else float("nan")


def main():
    R = load_races()
    for wn, lab in (("explore", "探索 2025"), ("confirm", "確認 2026-01〜08")):
        sold = [r for r in apply_cap(window(R, wn)) if r["settled"]]
        y = np.array([1.0 if r["pay"] >= r["inv"] else 0.0 for r in sold])
        day = np.array([r["date"] for r in sold])
        plan = np.array([r["plan"] for r in sold])
        vd = np.array([f'{r["date"]}/{r["venue"]}' for r in sold])
        print(f"\n{'='*100}\n■ {lab}  商品 {len(y):,}件 / {len(set(day))}日 / "
              f"全体の表示的中 {y.mean()*100:.2f}%\n{'='*100}")
        for name, strata in (("A 全体シャッフル（構成も日内も壊す）", None),
                             ("B プラン内シャッフル（構成を保つ）", plan)):
            o, med, hi, p = perm_test(y, day, strata)
            print(f"  {name:34s} phi={o:5.3f}  帰無中央 {med:5.3f} "
                  f"97.5%点 {hi:5.3f}  p={p:.4f}")
        # C 日内シャッフル: 会場-日のまとまりを壊す（日の合計は保つ）
        o, med, hi, p = perm_test(y, vd, day)
        print(f"  {'C 日内シャッフル（会場×日で測る）':34s} phi={o:5.3f}  "
              f"帰無中央 {med:5.3f} 97.5%点 {hi:5.3f}  p={p:.4f}")
        print(f"  対の相関 ICC: 同じ日 {icc_pairs(y, day):+.5f} / "
              f"同じ日×会場 {icc_pairs(y, vd):+.5f} / "
              f"同じプラン {icc_pairs(y, plan):+.5f}")
        # 日次の分布
        dg = by_group(y, day)
        n = np.array([len(v) for v in dg.values()])
        k = np.array([y[v].sum() for v in dg.values()])
        m = n >= 5
        rate = k[m] / n[m] * 100
        print(f"  日次の表示的中率: 中央 {np.median(rate):.2f}%  "
              f"IQR {np.percentile(rate,25):.1f}-{np.percentile(rate,75):.1f}  "
              f"SD {rate.std():.2f}pt  最小 {rate.min():.1f}%  最大 {rate.max():.1f}%")
        exp_sd = np.sqrt(y.mean() * (1 - y.mean()) / n[m].mean()) * 100
        print(f"  ├ 二項なら SD ≒ {exp_sd:.2f}pt（1日 {n[m].mean():.1f}件）")
        print(f"  └ 半減日(<{y.mean()*50:.1f}%) {(rate < y.mean()*50).mean()*100:.1f}%  "
              f"0件日 {(k[m]==0).mean()*100:.1f}%")


if __name__ == "__main__":
    main()
