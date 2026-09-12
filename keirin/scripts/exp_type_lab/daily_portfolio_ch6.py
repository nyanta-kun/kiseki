"""§6 残差 z にまだ構造が残っていないか（＝日次へ介入する余地があるか）。"""
import sys; sys.path.insert(0,'scripts/exp_type_lab')
import numpy as np
from collections import defaultdict
import daily_portfolio as D
rng = np.random.default_rng(11)

for nm, rows in (("探索 2024-07〜2025-12", D.EX), ("確認 2026-01〜08", D.CF)):
    sold = D.produce(rows)
    base = {}
    for p in {r['plan'] for r in sold}:
        s = [r for r in sold if r['plan'] == p]
        base[p] = sum(1 for r in s if r['pay'] > r['inv']) / len(s)
    dd = defaultdict(list)
    for r in sold:
        dd[r['date']].append(r)
    days, z, feat = [], [], defaultdict(list)
    for d, s in dd.items():
        n = len(s); e = sum(base[r['plan']] for r in s)
        o = sum(1 for r in s if r['pay'] > r['inv'])
        v = sum(base[r['plan']] * (1 - base[r['plan']]) for r in s)
        if v <= 0: continue
        days.append(d); z.append((o - e) / np.sqrt(v))
        feat["件数"].append(n)
        feat["型F比率"].append(sum(1 for r in s if r['type'] == 'F') / n)
        feat["平均軸信頼"].append(np.mean([r['axis'] for r in s]))
        feat["平均rp_sd"].append(np.mean([r['rp_sd'] for r in s]))
        feat["会場数"].append(len({r['venue'] for r in s}))
        feat["1会場あたり件数"].append(n / len({r['venue'] for r in s}))
        feat["平均開催日目"].append(np.mean([r['dayidx'] for r in s]))
        feat["夜開催の比率"].append(sum(1 for r in s if r['wave'] == 'night') / n)
        feat["曜日(土日=1)"].append(
            1.0 if np.datetime64(d).astype('datetime64[D]').astype(int) % 7 in (2, 3) else 0.0)
    z = np.array(z)
    print(f"\n■ {nm}  {len(z)}日  z: 平均{z.mean():+.3f} SD{z.std(ddof=1):.3f}"
          f"（構造が無ければ 0±1）  χ²/df={np.mean(z**2):.3f}")
    print(f"  {'日ごとの説明変数':18s} {'r(z との相関)':>14s} {'95%CI':>22s}")
    for k, v in feat.items():
        v = np.array(v, dtype=float)
        r = float(np.corrcoef(v, z)[0, 1])
        bs = [float(np.corrcoef(v[i], z[i])[0, 1])
              for i in (rng.integers(0, len(z), len(z)) for _ in range(600))]
        lo, hi = np.percentile(bs, [2.5, 97.5])
        flag = "  ← 0を跨がない" if lo * hi > 0 else ""
        print(f"  {k:18s} {r:14.4f} [{lo:+.4f},{hi:+.4f}]{flag}")
