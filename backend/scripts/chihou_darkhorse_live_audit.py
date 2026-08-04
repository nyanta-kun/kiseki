"""本番判定（odds_history 最新）と検証時の判定（race_results 最終オッズ）の差を監査する。

## なぜ必要か

検証（`chihou_darkhorse_place.py`）は `chihou.race_results.win_odds`（確定オッズ）で
条件を評価した。しかし本番 `chihou_is_open_place()` は
`chihou.odds_history` の最新スナップショットを見る。この 2 つがずれていれば、
**本番は検証したのと別の馬を選んでいる**ことになる。

実測（2026-05〜07・36,679行）: 完全一致 95.4%、差がある 1,695 行。
差の多くは 999.9 のような未確定値で、単勝30-50倍帯でも 3,882 行中 221 行がずれる。

## 出すもの

  1. 本番パス／検証パスそれぞれの選択馬と複勝ROI
  2. 片方にしか入らない馬（取りこぼし・誤選択）の実績
  3. 最終オッズが帯から外れた馬をどれだけ拾っているか

使い方:
  cd backend
  .venv/bin/python scripts/chihou_darkhorse_live_audit.py --start 20260501 --end 20260731
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_root.parent / ".env")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402

from src.indices.buy_signal import chihou_is_open_place, chihou_market_top3_share  # noqa: E402

RNG = np.random.default_rng(0)

QUERY = """
WITH oh AS (
  SELECT DISTINCT ON (o.race_id, o.combination)
         o.race_id, o.combination::int AS hn, o.odds AS live_odds
  FROM chihou.odds_history o
  JOIN chihou.races r ON r.id = o.race_id
  WHERE o.bet_type = 'win' AND r.date BETWEEN %(start)s AND %(end)s
  ORDER BY o.race_id, o.combination, o.fetched_at DESC
),
pay AS (
  SELECT p.race_id, p.combination::int AS hn, p.payout / 100.0 AS place_ret
  FROM chihou.race_payouts p
  JOIN chihou.races r ON r.id = p.race_id
  WHERE p.bet_type = 'place' AND r.date BETWEEN %(start)s AND %(end)s
    AND p.combination ~ '^[0-9]+$'
)
SELECT r.id AS race_id, r.date, r.course_name, r.race_number, r.head_count,
       rr.horse_number AS hn, rr.win_odds AS final_odds, oh.live_odds,
       rr.finish_position, COALESCE(pay.place_ret, 0.0) AS place_ret,
       (SELECT count(*) FROM chihou.race_payouts p2
         WHERE p2.race_id = r.id AND p2.bet_type = 'place') AS n_pay
FROM chihou.races r
JOIN chihou.race_results rr ON rr.race_id = r.id
LEFT JOIN oh ON oh.race_id = r.id AND oh.hn = rr.horse_number
LEFT JOIN pay ON pay.race_id = r.id AND pay.hn = rr.horse_number
WHERE r.date BETWEEN %(start)s AND %(end)s
  AND r.course <> '83'
  AND rr.finish_position IS NOT NULL
  AND COALESCE(rr.abnormality_code, 0) = 0
"""


def _ci(v: np.ndarray) -> tuple[float, float, float]:
    if len(v) == 0:
        return 0.0, 0.0, 0.0
    b = RNG.choice(v, size=(4000, len(v)), replace=True).mean(axis=1)
    return float(v.mean()), *np.percentile(b, [2.5, 97.5])


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    args = p.parse_args()

    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(QUERY, {"start": args.start, "end": args.end})
    df = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
    cur.close()
    conn.close()

    for col in ("final_odds", "live_odds", "place_ret", "head_count", "finish_position", "n_pay"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # 複勝払戻レコードのあるレースのみ（払戻未取込を「全馬外れ」と誤読しないため）
    df = df[df["n_pay"] > 0].copy()
    print(f"対象: {len(df):,}行 / {df['race_id'].nunique():,}レース ({args.start}〜{args.end})")

    # レース単位で share を 2 通り算出
    share_final = df.groupby("race_id")["final_odds"].apply(chihou_market_top3_share)
    share_live = df.groupby("race_id")["live_odds"].apply(chihou_market_top3_share)
    df["share_final"] = df["race_id"].map(share_final)
    df["share_live"] = df["race_id"].map(share_live)

    df["sel_final"] = [
        chihou_is_open_place(o, s, h)
        for o, s, h in zip(df["final_odds"], df["share_final"], df["head_count"], strict=True)
    ]
    df["sel_live"] = [
        chihou_is_open_place(o, s, h)
        for o, s, h in zip(df["live_odds"], df["share_live"], df["head_count"], strict=True)
    ]

    print(f"\n{'=' * 88}")
    print("  検証パス（確定オッズ） vs 本番パス（odds_history 最新）")
    print(f"{'=' * 88}")
    for lab, mask in [("検証パス", df["sel_final"]), ("本番パス", df["sel_live"])]:
        sub = df[mask]
        r, lo, hi = _ci(sub["place_ret"].values)
        hit = (sub["place_ret"] > 0).mean() if len(sub) else 0.0
        print(f"  {lab}: n={len(sub):>4}  複勝率={hit:.4f}  複勝ROI={r:.3f}  95%CI[{lo:.3f}, {hi:.3f}]")

    both = df[df["sel_final"] & df["sel_live"]]
    only_live = df[df["sel_live"] & ~df["sel_final"]]
    only_final = df[df["sel_final"] & ~df["sel_live"]]
    print(f"\n  両方が選ぶ:       n={len(both):>4}  複勝ROI={both['place_ret'].mean():.3f}")
    print(f"  本番だけが選ぶ:   n={len(only_live):>4}  複勝ROI="
          f"{only_live['place_ret'].mean() if len(only_live) else 0:.3f}  ← 誤選択の疑い")
    print(f"  検証だけが選ぶ:   n={len(only_final):>4}  複勝ROI="
          f"{only_final['place_ret'].mean() if len(only_final) else 0:.3f}  ← 取りこぼし")

    if len(only_live):
        print("\n  本番だけが選んだ馬の最終オッズ分布:")
        print(f"    最終<30: {(only_live['final_odds'] < 30).sum()}  "
              f"30-50: {((only_live['final_odds'] >= 30) & (only_live['final_odds'] < 50)).sum()}  "
              f"50+: {(only_live['final_odds'] >= 50).sum()}  "
              f"欠損: {only_live['final_odds'].isna().sum()}")
        print(f"    live/final 中央値: {only_live['live_odds'].median():.1f} / "
              f"{only_live['final_odds'].median():.1f}")


if __name__ == "__main__":
    main()
