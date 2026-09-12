#!/usr/bin/env python3
"""外れの中身をさらに割る — 相手（3着）と順番はどこまで拾えるのか（2026-09-10）。

① 相手: 軸2車がそろったのに集合を買えていない回で、**3着車は指数何番手だったか**。
   → 相手を1車ずつ増やしたときのカバレッジの天井（買えばよかったのか、読めないのか）。
② 順番: 集合を買えていた回で、**実際の並びは確率で何番目だったか**（1..6）。
   → 順番を1つずつ増やしたときの天井。
"""
from __future__ import annotations

import pickle
from collections import Counter

import numpy as np

ROWS = pickle.load(open("/tmp/miss_anatomy_rows.pkl", "rb"))
W = {w: [r for r in ROWS if r["win"] == w] for w in ("explore", "confirm")}


def main() -> None:
    for wn, lab in (("confirm", "確認 2026-01〜08"), ("explore", "探索 2024-07〜2025-12")):
        rows = W[wn]
        print("\n" + "=" * 100)
        print(f"=== {lab}   {len(rows):,}商品")
        print("=" * 100)

        # ① 相手 ────────────────────────────────────────────────────────
        both = [r for r in rows if r["both_in3"]]
        miss = [r for r in both if not r["in_legs"] and not r["set_hit"]]
        hitb = [r for r in both if r["in_legs"] or r["set_hit"]]
        print(f"\n■ ① 相手（3着）— 軸2車がそろった {len(both):,}件 "
              f"（全体の {len(both)/len(rows)*100:.1f}%）")
        print(f"   うち集合を買えていた {len(hitb):,} ({len(hitb)/len(both)*100:.1f}%) / "
              f"買えていない {len(miss):,} ({len(miss)/len(both)*100:.1f}%)")
        print("   3着車の指数順位（p3 降順・0=軸1）の分布:")
        cm, ch = Counter(r["third_rank"] for r in miss), Counter(r["third_rank"] for r in hitb)
        print(f"     {'順位':6s} {'買えた':>8s} {'買えなかった':>12s} {'買えなかった側の累積%':>22s}")
        tot = len(miss); cum = 0
        for k in range(7):
            cum += cm.get(k, 0)
            print(f"     {k+1:<6d} {ch.get(k,0):8,} {cm.get(k,0):12,} {cum/tot*100:22.1f}")
        # 相手を p3 上位 m 車に固定したときの天井
        print("   相手を『指数上位 m 車』に取ったときの、軸2車そろい時のカバレッジ天井:")
        for m in range(2, 8):
            cov = sum(1 for r in both if r["third_rank"] < m) / len(both) * 100
            print(f"     上位{m}車まで買う → {cov:5.1f}%   （全レース比 "
                  f"{sum(1 for r in both if r['third_rank'] < m)/len(rows)*100:5.2f}%）")

        # ② 順番 ────────────────────────────────────────────────────────
        print(f"\n■ ② 順番 — 集合（3車）が買えていた回の、実際の並びの確率順位")
        cov = [r for r in rows if r["set_hit"] or r["in_legs"]]
        c = Counter(r["perm_rank"] for r in cov)
        n = len(cov)
        print(f"   集合が当たった {n:,}件（全体の {n/len(rows)*100:.1f}%）")
        cum = 0
        for k in range(6):
            cum += c.get(k, 0)
            print(f"     確率 {k+1} 番目の並び: {c.get(k,0):6,} ({c.get(k,0)/n*100:5.1f}%)  "
                  f"累積 {cum/n*100:5.1f}%  → 全レース比 {cum/len(rows)*100:5.2f}%")

        # ③ 決着そのものが確率で何番目か（=点数のダイヤルの天井）────────
        print(f"\n■ ③ 決着（三連単210点）の確率順位 — 何点買えば届くか")
        rk = np.array([r["rank_prob"] for r in rows])
        for k in (1, 2, 3, 4, 5, 6, 8, 10, 12, 14, 20, 30, 50):
            print(f"     上位{k:3d}点 → {(rk < k).mean()*100:5.2f}%")


if __name__ == "__main__":
    main()
