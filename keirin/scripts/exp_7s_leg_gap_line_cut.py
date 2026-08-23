#!/usr/bin/env python3
"""7S の相手を**断層 × ライン**で切る（2026-08-23・ユーザー指示）。

## 指示

> 順位だけでは不足。**ライン、スコアの断層**を考慮した買い目の調整に意味がある。
> **大きく離れて力不足に見えるケースのみ除外**とし、残りの買い目を買う。

🔴 前回の測定（上位N・p3閾値・一律の落差カット）は**どれも ROI が動かず的中だけ
   下がった**。だがあれは「順位で機械的に削る」形。ここは
   **「断層で明らかに離れた車」だけを落とし、ラインで来る余地がある車は残す**。

## 切り方

1. 相手（軸2車を除く5車）を `p3` 降順に並べる
2. 隣接する差が **G 以上**になった箇所より下を「力不足」として除外候補にする
3. 🔴 **ライン保護**: 除外候補でも **軸1 または軸2 と同一ライン**なら残す
   （番手・3番手はライン戦で連れて来られる。得点だけで切ると根拠を失う）
4. 残った点を買う。**投資は 10,000円のまま**なので1点あたりの賭け金は上がる

## 測るもの

- **取りこぼし率**: 除外した車が実際に3着以内に入っていた割合（＝直接の損）
- 的中率 / ROI / ガミ率 / 中央払戻 / 日次100%超
🔴 探索 2024-01〜2025-12 / 確認 2026-01〜06（封印は読まない）
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7t1_rank_wt import _load_finishes  # noqa: E402
from scripts.exp_7s_leg_selection import CONFIRM, SEARCH_END, build  # noqa: E402
from scripts.exp_7s_stake_contrast import allocate  # noqa: E402
from scripts.exp_trio_joint_partner import load_boards  # noqa: E402
from src.result_top3 import winning_trifectas  # noqa: E402


def cut(r, gap, line_guard: bool):
    """→ (買う相手, 除外した相手)。断層より下を落とし、ラインで保護する。"""
    rest, p3, lg = r["rest"], r["p3"], r["lg"]
    keep = [rest[0]]
    dropped = []
    broken = False
    for prev, cur in zip(rest, rest[1:]):
        if not broken and p3[prev] - p3[cur] >= gap:
            broken = True
        (dropped if broken else keep).append(cur)
    if line_guard and dropped:
        la, lb = lg.get(r["a1"]), lg.get(r["a2"])
        saved = [c for c in dropped
                 if (la is not None and lg.get(c) == la)
                 or (lb is not None and lg.get(c) == lb)]
        if saved:
            keep = [c for c in rest if c in set(keep) | set(saved)]
            dropped = [c for c in dropped if c not in saved]
    return keep, dropped


def run(rows, board, fin, label, arms):
    print(f"\n===== {label} ・ {len(rows):,}R =====")
    print(f"{'切り方':>30}{'平均点':>7}{'取りこぼし':>10}{'的中%':>8}"
          f"{'ROI':>8}{'ガミ率':>8}{'中央払戻':>10}{'100%超の日':>11}")
    for name, gap, guard, alloc in arms:
        d = defaultdict(lambda: [0.0, 0.0])
        n = hit = gami = miss = 0
        legs_n, pays = [], []
        for r in rows:
            b = board.get(r["key"])
            o3 = fin.get(r["key"])
            if not b or not o3:
                continue
            if gap is None:
                keep, dropped = r["rest"], []
            else:
                keep, dropped = cut(r, gap, guard)
            ks = {c: frozenset((r["a1"], r["a2"], c)) for c in keep}
            ks = {c: k for c, k in ks.items() if k in b}
            if not ks:
                continue
            od = {c: b[k] for c, k in ks.items()}
            st = allocate(alloc, [c for c in r["rest"] if c in ks], od)
            if st is None:
                st = {c: max(100, (10000 // len(ks)) // 100 * 100) for c in ks}
            n += 1
            legs_n.append(len(ks))
            top3 = {c for w in winning_trifectas(o3) for c in w}
            # 取りこぼし＝除外した車が実際に3着内、かつ軸2車も3着内（買っていれば当たった）
            if (r["a1"] in top3 and r["a2"] in top3
                    and any(c in top3 for c in dropped)):
                miss += 1
            bet = sum(st.values())
            pay = sum(int(od[c] * 100) * st[c] // 100
                      for c, k in ks.items() if k in r["wins"])
            h = any(k in r["wins"] for k in ks.values())
            hit += h
            if h:
                pays.append(pay); gami += int(pay < bet)
            z = d[r["date"]]; z[0] += bet; z[1] += pay
        if n < 100:
            continue
        v = np.array([[x[0], x[1]] for x in d.values()], float)
        print(f"{name:>30}{np.mean(legs_n):>7.2f}{miss/n:>10.2%}{hit/n:>8.2%}"
              f"{v[:, 1].sum()/v[:, 0].sum():>8.1%}{gami/max(hit,1):>8.1%}"
              f"{np.median(pays):>10,.0f}{float(np.mean(v[:, 1] >= v[:, 0])):>11.1%}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.parse_args()
    arms = [("現行(5点総流し・均等)", None, False, "現行(均等)")]
    for g in (0.15, 0.20, 0.25, 0.30):
        arms.append((f"断層{g:.2f}（ライン保護なし）", g, False, "現行(均等)"))
    for g in (0.15, 0.20, 0.25, 0.30):
        arms.append((f"断層{g:.2f} ＋ライン保護", g, True, "現行(均等)"))
    arms.append(("断層0.20＋ライン保護＋C配分", 0.20, True, "C:下位3点を1.2倍"))
    arms.append(("断層0.25＋ライン保護＋C配分", 0.25, True, "C:下位3点を1.2倍"))
    for label, (lo, hi) in (("探索 2024-01〜2025-12", ("2024-01-01", SEARCH_END)),
                            ("確認 2026-01〜06", CONFIRM)):
        rows = build(lo, hi)
        run(rows, load_boards([r["key"] for r in rows]),
            _load_finishes([r["key"] for r in rows]), label, arms)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
