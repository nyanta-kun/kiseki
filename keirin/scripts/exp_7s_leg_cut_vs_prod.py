#!/usr/bin/env python3
"""7S の相手カットを **本番と同じベースライン** で測る（2026-08-23・再測定）。

## 🔴 前回までの測定は baseline が誤っていた

「現行＝均等 2,000円 × 5点」として測っていたが、**本番は既にダッチング配分**。
`RANK_CONFIGS["7S"]` に `"tilt_stakes": True` があり、
`_build_tilted_legs` → `tilted_stakes` → `landing_weights` が
**予測オッズがあれば `1/オッズ` を単独採用**する（＝全点の払戻を揃える）。

本日の実入稿（防府12R）:

    1=4=5 4,500円(3.8倍) / 1=2=5 2,300円(7.4倍) / 1=5=6 1,400円(12.0倍)
    1=5=7 1,100円(16.5倍) / 1=3=5 700円(24.6倍)  → どこが当たっても約17,000円

`RANK_7S_STAKE = unit_stake(5)` は**旧実験スクリプトでしか使われていない**。
そこから baseline を取ったのが誤りだった。

## この再測定でやること

**配分は両腕とも本番関数 `tilted_stakes` をそのまま呼ぶ。** 変えるのは相手の集合だけ。

| 腕 | 相手 |
|---|---|
| 現行 | 残り5車の総流し |
| 断層G ＋ライン保護 | `p3` の断層で切り、軸1/軸2と同ラインの車は残す |

⚠️ `morning_odds` は渡さない（過去の朝板は 61% のレースにしか無く、
   確定オッズを代入すると先読みになる）。予測オッズが作れないときは
   本番と同じく `top3_probs` 由来の重みへ落ちる。
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_7t1_rank_wt import _load_finishes  # noqa: E402
from scripts.exp_7s_leg_gap_line_cut import cut  # noqa: E402
from scripts.exp_7s_leg_selection import CONFIRM, SEARCH_END, build  # noqa: E402
from scripts.exp_trio_joint_partner import load_boards  # noqa: E402
from src.odds_prediction import OddsPredictionUnavailable, predicted_trio_board  # noqa: E402
from src.result_top3 import winning_trifectas  # noqa: E402
from src.stake_allocation import tilted_stakes  # noqa: E402

ARMS = [("現行(5点総流し)", None, False)]
for g in (0.20, 0.25, 0.30):
    ARMS.append((f"断層{g:.2f}（ライン保護なし）", g, False))
for g in (0.20, 0.25, 0.30):
    ARMS.append((f"断層{g:.2f} ＋ライン保護", g, True))


def ci(days_a, days_b, B=4000, seed=241):
    ks = sorted(set(days_a) & set(days_b))
    v = np.array([[days_a[k][0], days_a[k][1], days_b[k][1]] for k in ks], float)
    rng = np.random.default_rng(seed)
    i = rng.integers(0, len(v), size=(B, len(v)))
    t = v[i, 0].sum(1)
    z = np.sort(v[i, 2].sum(1) / t - v[i, 1].sum(1) / t)
    return z[int(B * .025)], z[int(B * .975)]


def run(rows, board, fin, label, pred):
    print(f"\n===== {label} ・ {len(rows):,}R（配分は本番の tilted_stakes）=====")
    print(f"{'相手の切り方':>24}{'平均点':>7}{'取りこぼし':>10}{'的中%':>8}{'ROI':>8}"
          f"{'（対現行）':>24}{'ガミ率':>8}{'中央払戻':>10}{'100%超の日':>11}")
    base_days = None
    base_roi = None
    for name, gap, guard in ARMS:
        d = defaultdict(lambda: [0.0, 0.0])
        n = hit = gami = miss = 0
        legs_n, pays = [], []
        for r in rows:
            b = board.get(r["key"]); o3 = fin.get(r["key"])
            if not b or not o3:
                continue
            keep, dropped = (r["rest"], []) if gap is None else cut(r, gap, guard)
            ks = {c: frozenset((r["a1"], r["a2"], c)) for c in keep}
            ks = {c: k for c, k in ks.items() if k in b}
            if not ks:
                continue
            pb = pred.get(r["key"])
            po = ({c: pb[ks[c]] for c in ks}
                  if pb and all(ks[c] in pb for c in ks) else None)
            st, _src = tilted_stakes(
                list(ks), None, {c: r["p3"][c] for c in ks},
                budget=10_000, predicted_odds=po)
            n += 1
            legs_n.append(len(ks))
            top3 = {c for w in winning_trifectas(o3) for c in w}
            if (r["a1"] in top3 and r["a2"] in top3
                    and any(c in top3 for c in dropped)):
                miss += 1
            bet = sum(st.values())
            pay = sum(int(b[k] * 100) * st[c] // 100
                      for c, k in ks.items() if k in r["wins"])
            h = any(k in r["wins"] for k in ks.values())
            hit += h
            if h:
                pays.append(pay); gami += int(pay < bet)
            z = d[r["date"]]; z[0] += bet; z[1] += pay
        if n < 100:
            continue
        v = np.array([[x[0], x[1]] for x in d.values()], float)
        roi = v[:, 1].sum() / v[:, 0].sum()
        if gap is None:
            base_days, base_roi = d, roi
            cell = ""
        else:
            lo, hi = ci(base_days, d)
            f = "🟢" if lo > 0 else ("🔴" if hi < 0 else "")
            cell = f"{(roi-base_roi)*100:+.1f}pt[{lo*100:+.1f},{hi*100:+.1f}]{f}"
        print(f"{name:>24}{np.mean(legs_n):>7.2f}{miss/n:>10.2%}{hit/n:>8.2%}"
              f"{roi:>8.1%}{cell:>24}{gami/max(hit,1):>8.1%}"
              f"{np.median(pays):>10,.0f}{float(np.mean(v[:, 1] >= v[:, 0])):>11.1%}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", default="both")
    args = ap.parse_args()
    wins = [("探索 2024-01〜2025-12", ("2024-01-01", SEARCH_END)),
            ("確認 2026-01〜06", CONFIRM)]
    if args.windows == "confirm":
        wins = wins[1:]
    for label, (lo, hi) in wins:
        rows = build(lo, hi)
        board = load_boards([r["key"] for r in rows])
        fin = _load_finishes([r["key"] for r in rows])
        pred = {}
        for r in rows:
            try:
                pred[r["key"]] = predicted_trio_board(r["key"])
            except (OddsPredictionUnavailable, Exception):
                pred[r["key"]] = None
        got = sum(1 for v in pred.values() if v)
        print(f"\n[{label}] 予測オッズが作れたレース {got:,}/{len(rows):,}"
              f"（{got/len(rows):.1%}）")
        run(rows, board, fin, label, pred)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
