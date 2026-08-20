#!/usr/bin/env python3
"""【優先順位の全ランク見直し】最低希望オッズ 1.5倍を基準に総当たりで直接対決する。

## 前提（2026-08-21 ユーザー方針）

- **最低限の希望オッズは 1.5 倍**（予測オッズのブレを織り込んだ設定）。
  したがって主指標は **「払戻 >= 1.5 × 賭け金 で的中した率」**。
- 的中率そのもの（ガミ込み）には意味が無い。件数は減ってよい。

従来の採否は **表示的中（払戻 > 賭け金）** で決めていたが、その差の大半は
**2倍未満の的中**で出来ていた（7S は的中の 66〜68% が2倍未満）。
基準を変えると優先順位の判断が変わるため、総当たりで測り直す。

## 何を測るか

同一レースに複数ランクが候補を持つ場合、入稿されるのは `RANK_ORDER` で
勝った1つだけ。したがって比較すべきは**競合したレースに限った直接対決**で、
「A を出す代わりに B を出していたらどうだったか」を同じレース集合で見る。

    勝率(>=1.5倍) の差 (B − A) と、レース単位 bootstrap の 95%CI

⚠️ **個別レースで判断しないこと。** 両方的中したレースでは点数の少ない側の
   払戻が構造的に大きくなるので、事例は必ず片方に有利に見える。

⚠️ 母集団は `picks_history` の本番記録で、**商品世代が混ざる**
   （7C の三連単停止 2026-08-17 / 7S の統合 2026-08-14 など）。
   順位の目安には使えるが、確定値として扱わないこと。

DB は読み取り専用 SELECT のみ。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_rank_priority_matrix.py \
        --from 2025-01-01 --to 2026-08-19 --mult 1.5
"""
from __future__ import annotations

import argparse
import random
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.database import get_connection  # noqa: E402

#: 入稿の優先順位（`netkeirin_submit_wt.RANK_ORDER`）。上ほど優先。
CURRENT_ORDER = ["7H2", "9H1", "7S", "9C", "7C", "7T1", "7B", "7H1", "7M1"]
MIN_CONFLICT = 60          # これ未満の競合は判定しない（小標本セルを作らない）


def load(d1: str, d2: str):
    """race_key(接尾辞なし) -> {rank: (bet, pay)}"""
    rows: dict[str, dict[str, tuple[int, int]]] = defaultdict(dict)
    with get_connection() as c:
        for r in c.execute(
                "SELECT split_part(race_key,'#',1) rk, rank, bet_amount, payout "
                "FROM picks_history WHERE race_date BETWEEN ? AND ? AND bet_amount > 0",
                (d1, d2)):
            name = str(r["rank"]).replace("RANK_", "")
            rows[str(r["rk"])][name] = (int(r["bet_amount"]), int(r["payout"] or 0))
    return rows


def rate(rows, keys, rank, mult):
    n = sum(1 for k in keys
            if rows[k][rank][1] >= mult * rows[k][rank][0] and rows[k][rank][1] > 0)
    return 100.0 * n / len(keys)


def boot(rows, keys, a, b, mult, n_iter=2000, seed=17):
    rnd = random.Random(seed)
    fa = {k: int(rows[k][a][1] >= mult * rows[k][a][0] and rows[k][a][1] > 0) for k in keys}
    fb = {k: int(rows[k][b][1] >= mult * rows[k][b][0] and rows[k][b][1] > 0) for k in keys}
    d = []
    for _ in range(n_iter):
        s = [keys[rnd.randrange(len(keys))] for _ in keys]
        d.append(100.0 * (sum(fb[k] for k in s) - sum(fa[k] for k in s)) / len(s))
    d.sort()
    return d[int(.025 * len(d))], d[int(.975 * len(d))]


def roi(rows, keys, rank):
    bet = sum(rows[k][rank][0] for k in keys)
    pay = sum(rows[k][rank][1] for k in keys)
    return 100.0 * pay / bet if bet else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d1", default="2025-01-01")
    ap.add_argument("--to", dest="d2", default="2026-08-19")
    ap.add_argument("--mult", type=float, default=1.5,
                    help="最低希望オッズ（既定 1.5・ユーザー方針 2026-08-21）")
    a = ap.parse_args()
    M = a.mult

    rows = load(a.d1, a.d2)
    ranks = sorted({rk for v in rows.values() for rk in v},
                   key=lambda x: CURRENT_ORDER.index(x) if x in CURRENT_ORDER else 99)
    print(f"[{a.d1}〜{a.d2}] レース {len(rows):,} / ランク {ranks}")
    print(f"基準: 払戻 >= {M} × 賭け金 で的中\n")

    print("===== 単独成績（全出番・参考）=====")
    print(f"  {'ランク':<8}{'R':>7}{f'{M}倍+%':>9}{'ROI%':>8}")
    for rk in ranks:
        keys = [k for k, v in rows.items() if rk in v]
        if len(keys) < MIN_CONFLICT:
            continue
        print(f"  {rk:<8}{len(keys):>7}{rate(rows, keys, rk, M):>9.2f}{roi(rows, keys, rk):>8.1f}")

    print(f"\n===== 直接対決（競合レースのみ・{M}倍以上で的中）=====")
    print("  差は「下の段(B) − 上の段(A)」。* は 95%CI が 0 を跨がない\n")
    print(f"  {'A(現行で優先)':<10}{'B':<10}{'競合R':>7}{'A%':>8}{'B%':>8}"
          f"{'差(B-A)':>10}{'95%CI':>18}{'ROI A/B':>16}")
    print("  " + "-" * 88)
    wins = defaultdict(int)
    for i, a_rk in enumerate(ranks):
        for b_rk in ranks[i + 1:]:
            keys = [k for k, v in rows.items() if a_rk in v and b_rk in v]
            if len(keys) < MIN_CONFLICT:
                continue
            ra, rb = rate(rows, keys, a_rk, M), rate(rows, keys, b_rk, M)
            lo, hi = boot(rows, keys, a_rk, b_rk, M)
            sig = "*" if lo > 0 or hi < 0 else " "
            if lo > 0:
                wins[b_rk] += 1
            elif hi < 0:
                wins[a_rk] += 1
            print(f"  {a_rk:<10}{b_rk:<10}{len(keys):>7}{ra:>8.2f}{rb:>8.2f}"
                  f"{rb - ra:>+10.2f}{f'[{lo:+.2f},{hi:+.2f}]':>17}{sig}"
                  f"{roi(rows, keys, a_rk):>8.1f}/{roi(rows, keys, b_rk):<7.1f}")

    if wins:
        print("\n  有意に勝った回数: "
              + " / ".join(f"{k} {v}" for k, v in sorted(wins.items(), key=lambda x: -x[1])))
    print("\n  ⚠️ 総当たりは多重比較になる。順位の入れ替えは**事前に仮説を立てた1組**を")
    print("     別窓で検定してから決めること（この表は候補の絞り込みにのみ使う）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
