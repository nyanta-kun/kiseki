#!/usr/bin/env python3
"""7H1 が見送ったレースを誰が拾い、その結果どうなるかを測る。

netkeirin は1レース1商品で、優先順位は `RANK_CONFIGS` の定義順
（`enabled=false` は飛ばされる）。2026-08-25 現在の実効順は
  7T1 > 7T3 > 7S > 9C > 7B > 7C > **7H1** > 7M1
なので、7H1 の下に居るのは **7M1 だけ**。7H1 が見送ったレースは
7M1 に候補があればそれが入稿され、無ければ**その日その レースは無商品**になる。

⚠️ ゲートは `continue` で抜ける規約なので、見送り＝そのレース自体を放棄ではない
   （`netkeirin_submit_wt.py` L1964）。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/exp_7h1_gate/handoff.py --key fav_ppw_norm --th 0.50
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.database import get_connection  # noqa: E402

CACHE = REPO / "data" / "exp" / "7h1_gate_cache.jsonl"
# 7H1 より**上**に居る7車ランク（この行があるレースは 7H1 は元々入稿されない）
HIGHER = ("RANK_7T1", "RANK_7T3", "RANK_7S", "RANK_7B", "RANK_7C")
LOWER = "RANK_7M1"


def load_cache():
    rows = []
    for line in CACHE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            if r.get("selected") and r.get("scored"):
                rows.append(r)
    return rows


def load_picks(dates: tuple[str, str]):
    by: dict[str, dict[str, dict]] = defaultdict(dict)
    with get_connection() as c:
        q = ("select race_date,race_key,rank,hit,payout,bet_amount from picks_history "
             "where race_date between ? and ? and rank in (%s)"
             % ",".join("?" * (len(HIGHER) + 1)))
        for r in c.execute(q, (dates[0], dates[1], *HIGHER, LOWER)):
            by[r["race_key"].split("#")[0]][r["rank"]] = dict(r)
    return by


def agg(items, get):
    n = hit = pay = bet = 0
    for it in items:
        v = get(it)
        if v is None:
            continue
        n += 1
        hit += v["hit"]
        pay += v["payout"]
        bet += v["bet_amount"]
    return dict(n=n, hit=hit, hit_rate=hit / n * 100 if n else 0.0,
                roi=pay / bet * 100 if bet else 0.0, pay=pay, bet=bet)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default="fav_ppw_norm")
    ap.add_argument("--th", type=float, default=0.50)
    ap.add_argument("--from", dest="dfrom", default="2025-01-01",
                    help="7M1 の picks_history は 2025-01 以降しか無い")
    ap.add_argument("--to", dest="dto", default="2026-12-31")
    args = ap.parse_args()

    rows = [r for r in load_cache() if args.dfrom <= r["race_date"] <= args.dto]
    picks = load_picks((args.dfrom, args.dto))

    # 7H1 が実際に入稿を取るレース（上位ランクに候補が無い）
    own = [r for r in rows if not any(h in picks.get(r["race_key"], {}) for h in HIGHER)]
    print(f"7H1 選別 {len(rows)}件 / うち上位ランクに取られない（＝7H1が入稿）"
          f" {len(own)}件 ({len(own) / max(len(rows), 1) * 100:.1f}%)")

    drop = [r for r in own if r[args.key] >= args.th]
    keep = [r for r in own if r[args.key] < args.th]
    print(f"\n見送り規則: {args.key} >= {args.th}")
    print(f"  残す {len(keep)}件 / 見送る {len(drop)}件")

    print("\n-- 見送る {}件で何が起きるか --".format(len(drop)))
    a7h1 = agg(drop, lambda r: dict(hit=r["hit"], payout=r["payout"],
                                    bet_amount=r["bet_amount"]))
    print(f"  現行(7H1が買う): n={a7h1['n']} 的中{a7h1['hit']} "
          f"({a7h1['hit_rate']:.2f}%) ROI={a7h1['roi']:.1f}% "
          f"払戻{a7h1['pay']:,}円 / 投資{a7h1['bet']:,}円")
    a7m1 = agg(drop, lambda r: picks.get(r["race_key"], {}).get(LOWER))
    print(f"  7M1が拾う分: n={a7m1['n']} ({a7m1['n'] / max(len(drop), 1) * 100:.1f}%が"
          f"7M1に候補あり) 的中{a7m1['hit']} ({a7m1['hit_rate']:.2f}%) "
          f"ROI={a7m1['roi']:.1f}% 払戻{a7m1['pay']:,}円 / 投資{a7m1['bet']:,}円")
    print(f"  7M1に候補が無い（＝無商品になる）: {len(drop) - a7m1['n']}件")

    print("\n-- 残す{}件（新しい7H1）--".format(len(keep)))
    ak = agg(keep, lambda r: dict(hit=r["hit"], payout=r["payout"],
                                  bet_amount=r["bet_amount"]))
    print(f"  n={ak['n']} 的中{ak['hit']} ({ak['hit_rate']:.2f}%) ROI={ak['roi']:.1f}%")
    ao = agg(own, lambda r: dict(hit=r["hit"], payout=r["payout"],
                                 bet_amount=r["bet_amount"]))
    print(f"  （比較）現行の7H1全体: n={ao['n']} 的中{ao['hit']} "
          f"({ao['hit_rate']:.2f}%) ROI={ao['roi']:.1f}%")


if __name__ == "__main__":
    main()
