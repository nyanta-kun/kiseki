#!/usr/bin/env python3
"""PR #275（軸2差し替え）の ROI を **本番の配分** で測り直す（2026-08-23・訂正）。

## なぜ測り直すか

PR #275 で報告した ROI（探索 61.4→76.0% / 確認 71.4→77.3%）は
**均等配分 `unit_stake(5)` で計算していた**。本番の 7S は
`RANK_CONFIGS["7S"]["tilt_stakes"] = True` で**ダッチング配分**（`tilted_stakes` →
`landing_weights` が予測オッズの `1/オッズ` を単独採用）。**別の商品を測っていた。**

🔴 **的中率（＝5点総流しの的中条件＝二軸的中）は配分に依存しない**ので、
   採否の根拠にした +5.36〜6.61pt は影響を受けない。訂正が要るのは ROI の数字だけ。

ここでは両腕とも本番の `tilted_stakes` を通し、予測オッズで配分して確定オッズで採点する。
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.exp_axis2_gap_line_confirm import GAP_SMALL  # noqa: E402
from scripts.exp_axis2_rule_product import rows_2026, rows_2425  # noqa: E402
from scripts.exp_trio_joint_partner import load_boards  # noqa: E402
from src.odds_prediction import OddsPredictionUnavailable, predicted_trio_board  # noqa: E402
from src.stake_allocation import tilted_stakes  # noqa: E402


def ci(days, B=4000, seed=263):
    v = np.array([[d[0], d[1], d[2]] for d in days.values()], float)
    rng = np.random.default_rng(seed)
    i = rng.integers(0, len(v), size=(B, len(v)))
    t = v[i, 0].sum(1)
    z = np.sort(v[i, 2].sum(1) / t - v[i, 1].sum(1) / t)
    return z[int(B * .025)], z[int(B * .975)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=400)
    args = ap.parse_args()
    print("【PR #275 の規則を本番の配分（tilted_stakes・予測オッズ）で測り直す】")
    print(f"{'窓':>16}{'件数':>7}{'現行的中':>9}{'置換的中':>9}{'Δpt':>8}"
          f"{'現行ROI':>9}{'置換ROI':>9}{'（ROI差）':>26}{'現ガミ':>8}{'置ガミ':>8}")
    for name, rows in (("探索(2026)", rows_2026("data/exp/trio_rank_cache.jsonl", args.rounds)),
                       ("確認(2024-25)", rows_2425(args.rounds))):
        rule = [r for r in rows
                if r["gap"] < GAP_SMALL and r["sl_rep"] and not r["sl_axes"]]
        board = load_boards([r["key"] for r in rule])
        d = defaultdict(lambda: [0.0, 0.0, 0.0])
        n = hc = hn = gc = gn = 0
        for r in rule:
            b = board.get(r["key"])
            if not b:
                continue
            try:
                pb = predicted_trio_board(r["key"])
            except (OddsPredictionUnavailable, Exception):
                pb = None

            def buy(a2):
                rest = [c for c in r["all"] if c not in (r["a1"], a2)]
                ks = {c: frozenset((r["a1"], a2, c)) for c in rest}
                ks = {c: k for c, k in ks.items() if k in b}
                if len(ks) < 5:
                    return None
                po = ({c: pb[ks[c]] for c in ks}
                      if pb and all(ks[c] in pb for c in ks) else None)
                st, _ = tilted_stakes(list(ks), None,
                                      {c: 1.0 / max(len(ks), 1) for c in ks},
                                      budget=10_000, predicted_odds=po)
                pay = sum(int(b[k] * 100) * st[c] // 100
                          for c, k in ks.items() if k in r["wins"])
                return sum(st.values()), pay, int(any(k in r["wins"] for k in ks.values()))
            cur, new = buy(r["a2"]), buy(r["rep"])
            if cur is None or new is None:
                continue
            n += 1
            hc += cur[2]; hn += new[2]
            gc += int(cur[2] and cur[1] < cur[0])
            gn += int(new[2] and new[1] < new[0])
            z = d[r["date"]]; z[0] += cur[0]; z[1] += cur[1]; z[2] += new[1]
        if n < 50:
            continue
        v = np.array([[x[0], x[1], x[2]] for x in d.values()], float)
        rc = v[:, 1].sum() / v[:, 0].sum(); rn = v[:, 2].sum() / v[:, 0].sum()
        lo, hi = ci(d)
        f = "🟢" if lo > 0 else ("🔴" if hi < 0 else "")
        print(f"{name:>16}{n:>7,}{hc/n:>9.2%}{hn/n:>9.2%}{(hn-hc)/n*100:>+8.2f}"
              f"{rc:>9.1%}{rn:>9.1%}"
              f"{f'{(rn-rc)*100:+.1f}pt[{lo*100:+.1f},{hi*100:+.1f}]{f}':>26}"
              f"{gc/max(hc,1):>8.1%}{gn/max(hn,1):>8.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
