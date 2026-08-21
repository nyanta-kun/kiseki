#!/usr/bin/env python3
"""波乱スコアの効果量が vintage を替えても安定するか（2026-08-21）。

## なぜこれを先にやるか

[[keirin_verification_design_audit_2026_08_21]] の指摘:

    同一窓(2026)・同一運用点で、両方 honest な2つの vintage が正反対を出す
      v2312(26,722R学習)  +0.26pt (帰無 52.3%点)
      v2412(53,468R学習)  +2.81pt (帰無 94.1%点)
    しかもスコアの順位相関は Spearman 0.886〜0.905

**0.9 相関の並べ替えで Δ が 2.5pt 動く量をゲートにしてはいけない。**
ここが落ちれば 7S のレース選別の話は終わる。

## 設計

- **3本の vintage が全部 honest な共通窓**だけを使う。学習終端の最大が
  2025-06-30 なので **窓 = 2025-07-01 〜 2026-08-20**
- 運用点は**相対上位25% / 50%**（絶対閾値だと vintage ごとの較正差が混ざる）
- 🔴 vintage 同士は**同じレースを大きく共有する**ので、独立2標本の SE で
  比べてはいけない。**レース単位のペア bootstrap** で Δ の差の CI を出す
- 判定: 3本の Δ の**幅が ±1pt 以内**なら安定。ペア差の CI が 0 を外れたら不安定

DB は読み取りのみ。
"""
from __future__ import annotations

import math
import pickle
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.database import get_connection  # noqa: E402
from src.preprocessing.upset_features import (  # noqa: E402
    build_upset_row, feature_vector,
)

VINTAGES = [
    ("v2312", "lgbm_upset_screen_n15v2312", 26722),
    ("v2412", "lgbm_upset_screen_n15v2412", 53468),
    ("v2506", "lgbm_upset_screen_n15v2506", 66506),
]
D1, D2 = "2025-07-01", "2026-08-20"   # 3本とも honest な共通窓


def main() -> None:
    models = {t: pickle.load(open(REPO / "data" / "models" / f"{n}.pkl", "rb"))
              for t, n, _ in VINTAGES}

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT split_part(race_key,'#',1) rk, bet_amount, payout "
            "FROM picks_history WHERE rank='RANK_7S' AND bet_amount>0 "
            "  AND race_date BETWEEN ? AND ?", (D1, D2))
        picks = {r["rk"]: (int(r["bet_amount"]), int(r["payout"] or 0)) for r in cur}
        keys = list(picks)
        cur.execute("""
            SELECT e.race_key, r.n_entries, r.grade, r.race_type, r.day_index,
                   r.distance, r.start_at, e.frame_no, e.race_point, e.line_group,
                   e.line_size, e.style, e.player_class, e.s_count, e.b_count,
                   e.first_rate, e.third_rate, e.prediction_mark, e.finish_order,
                   v.bank_length, v.is_indoor
            FROM wt_entries e JOIN wt_races r USING(race_key)
            LEFT JOIN venue_info v ON v.venue_code=r.venue_id
            WHERE r.cancel=0 AND e.race_key = ANY(?)""", (keys,))
        ents: dict[str, list[dict]] = defaultdict(list)
        for e in cur:
            ents[e["race_key"]].append(dict(e))

    rk_list, ratio, score = [], [], {t: [] for t, _, _ in VINTAGES}
    for rk, es in ents.items():
        race = {k: es[0].get(k) for k in
                ("n_entries", "grade", "race_type", "day_index", "distance",
                 "start_at", "bank_length", "is_indoor")}
        f = build_upset_row(es, race)
        if f is None:
            continue
        x = np.array([feature_vector(f)], dtype=float)
        rk_list.append(rk)
        bet, pay = picks[rk]
        ratio.append(pay / bet)
        for t, _, _ in VINTAGES:
            score[t].append(float(models[t].predict(x)[0]))

    n = len(rk_list)
    base = 100.0 * sum(1 for r in ratio if r >= 2.0) / n
    print(f"共通 honest 窓 {D1}〜{D2}  7S {n}R  全件 2倍+ {base:.2f}%\n")

    def rank(v):
        o = sorted(range(len(v)), key=lambda i: v[i])
        r = [0] * len(v)
        for k, i in enumerate(o):
            r[i] = k
        return r

    def spear(a, b):
        ra, rb = rank(a), rank(b)
        ma, mb = sum(ra) / n, sum(rb) / n
        num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
        den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
        return num / den

    print("スコアの順位相関:")
    for i, (a, _, _) in enumerate(VINTAGES):
        for b, _, _ in VINTAGES[i + 1:]:
            print(f"  {a} × {b}  Spearman {spear(score[a], score[b]):+.4f}")

    for frac in (0.25, 0.50):
        k = int(n * frac)
        sel = {}
        print(f"\n=== 相対上位 {int(frac*100)}%（{k}R）===")
        print(f"{'vintage':<9}{'学習R':>8}{'2倍+':>9}{'Δ':>9}{'5倍+':>8}{'ROI':>8}")
        for t, _, ntr in VINTAGES:
            idx = sorted(range(n), key=lambda i: -score[t][i])[:k]
            sel[t] = set(idx)
            rs = [ratio[i] for i in idx]
            d = 100.0 * sum(1 for r in rs if r >= 2.0) / k - base
            print(f"{t:<9}{ntr:>8}"
                  f"{100.0*sum(1 for r in rs if r>=2.0)/k:>8.2f}%{d:>+8.2f}"
                  f"{100.0*sum(1 for r in rs if r>=5.0)/k:>7.2f}%"
                  f"{100.0*sum(rs)/k:>7.1f}%")
        # 🔴 同件数ランダム帰無での位置も出す。Δ が正でも帰無の中なら意味が無い。
        rng0 = random.Random(1)
        flags = [1.0 if r >= 2.0 else 0.0 for r in ratio]
        draws = sorted(100.0 * sum(rng0.sample(flags, k)) / k for _ in range(4000))
        ds = []
        print(f"  同件数ランダム帰無: 平均 {sum(draws)/len(draws):.2f}% / "
              f"SD {(sum((d-sum(draws)/len(draws))**2 for d in draws)/len(draws))**.5:.2f}pt "
              f"/ 90%点 {draws[3600]:.2f}% / 95%点 {draws[3800]:.2f}%")
        for t, _, _ in VINTAGES:
            idx = sel[t]
            act = 100.0 * sum(1 for i in idx if ratio[i] >= 2.0) / k
            pct = 100.0 * sum(1 for d in draws if d < act) / len(draws)
            print(f"    {t}: 実測 {act:.2f}% → 帰無の {pct:.1f}%点"
                  f"{'  ✅>95%点' if pct >= 95 else ''}")
            ds.append(act - base)
        print(f"  → Δ の幅 {max(ds)-min(ds):.2f}pt "
              f"（{'🟢 ±1pt 以内' if max(ds)-min(ds) <= 1.0 else '🔴 ±1pt 超'}）")

        print("  選ばれたレースの重なり(Jaccard) と Δ の差のペア bootstrap CI:")
        rng = random.Random(0)
        for i, (a, _, _) in enumerate(VINTAGES):
            for b, _, _ in VINTAGES[i + 1:]:
                jac = len(sel[a] & sel[b]) / len(sel[a] | sel[b])
                fa = [1.0 if (i2 in sel[a] and ratio[i2] >= 2.0) else 0.0
                      for i2 in range(n)]
                fb = [1.0 if (i2 in sel[b] and ratio[i2] >= 2.0) else 0.0
                      for i2 in range(n)]
                diffs = []
                for _ in range(3000):
                    s = [rng.randrange(n) for _ in range(n)]
                    ka = sum(1 for i2 in s if i2 in sel[a])
                    kb = sum(1 for i2 in s if i2 in sel[b])
                    if not ka or not kb:
                        continue
                    diffs.append(100.0 * sum(fa[i2] for i2 in s) / ka
                                 - 100.0 * sum(fb[i2] for i2 in s) / kb)
                diffs.sort()
                lo, hi = diffs[int(.025 * len(diffs))], diffs[int(.975 * len(diffs))]
                sig = "🔴 0を外れる" if (lo > 0 or hi < 0) else "0を含む"
                print(f"    {a}×{b}  Jaccard {jac:.3f}  "
                      f"Δ差 [{lo:+.2f}, {hi:+.2f}]  {sig}")


if __name__ == "__main__":
    main()
