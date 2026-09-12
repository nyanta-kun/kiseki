"""§8 日次表示的中の分散分解（構成 / 1商品ごとの運 / 残差）。"""
import sys; sys.path.insert(0,'scripts/exp_type_lab')
import numpy as np
from collections import defaultdict
import daily_portfolio as D

for nm, rows in (("探索 2024-07〜2025-12", D.EX), ("確認 2026-01〜08", D.CF)):
    for lab, sub in (("本番再現（軸ゲート+日次上限）", D.produce(rows)),
                     ("上限なし（軸ゲートのみ）", [r for r in rows if r["gate"]])):
        base = {}
        for p in {r['plan'] for r in sub}:
            s = [r for r in sub if r['plan'] == p]
            base[p] = sum(1 for r in s if r['pay'] > r['inv']) / len(s)
        dd = defaultdict(list)
        for r in sub:
            dd[r['date']].append(r)
        obs, exp, binv, ns = [], [], [], []
        for d, s in dd.items():
            n = len(s)
            obs.append(sum(1 for r in s if r['pay'] > r['inv']) / n * 100)
            exp.append(sum(base[r['plan']] for r in s) / n * 100)
            binv.append(sum(base[r['plan']] * (1 - base[r['plan']]) for r in s) / n**2 * 1e4)
            ns.append(n)
        obs, exp, binv = np.array(obs), np.array(exp), np.array(binv)
        vo, ve, vb = obs.var(ddof=1), exp.var(ddof=1), binv.mean()
        print(f"\n■ {nm} / {lab}  {len(obs)}日・{np.median(ns):.0f}件/日(中央)")
        print(f"   日次表示的中の実測分散      {vo:8.2f} (SD {np.sqrt(vo):5.2f}pt)  100.0%")
        print(f"   ├ ①型構成（その日に来たレース） {ve:8.2f} (SD {np.sqrt(ve):5.2f}pt)  {ve/vo*100:5.1f}%")
        print(f"   ├ ②1商品ごとの当たり外れ      {vb:8.2f} (SD {np.sqrt(vb):5.2f}pt)  {vb/vo*100:5.1f}%")
        print(f"   └ ③残差（①②で説明できない）   {vo-ve-vb:8.2f}"
              f" {'(SD %5.2fpt)'%np.sqrt(max(vo-ve-vb,0)) if vo-ve-vb>0 else '            '}"
              f"  {(vo-ve-vb)/vo*100:5.1f}%")
