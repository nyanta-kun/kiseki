"""発走前オッズだけで判定した場合の honest 性能を測る（本番と同じ情報条件）。

## 見つかった問題

`chihou_darkhorse_place.py` の検証は **確定オッズ**で条件を評価していた。
しかし馬券を買う時点で確定オッズは分からない。つまりあの 1.220 という数字は
**賭け時に得られない情報を使っていた**（軽度の look-ahead）。

さらに本番は `odds_history` の「最後のスナップショット」を見るが、これは
発走直前とは限らない。監査（`chihou_darkhorse_live_audit.py`, 2026-05〜07）では:

    検証パス（確定オッズ）: n=339  複勝ROI 1.120
    本番パス（最終スナップ）: n=398  複勝ROI 1.067
      本番だけが選ぶ 75頭 → 複勝ROI 0.739（live中央値35.8倍 / 最終中央値81.3倍）

**本番は「スナップショットでは35倍だが実際は81倍まで流れた馬」を掴んでいる。**
これがユーザーの言う「大きく外している」の実体。

## このスクリプトがやること

発走 N 分前のスナップショットだけで share とオッズを評価し、
払戻は実際の複勝払戻で計算する。これが本番で再現可能な唯一の正しい数字。

使い方:
  cd backend
  .venv/bin/python scripts/chihou_darkhorse_prerace.py --start 20260101 --end 20260430   # 探索
  .venv/bin/python scripts/chihou_darkhorse_prerace.py --start 20260501 --end 20260731   # 確認
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

from src.indices.buy_signal import chihou_market_top3_share  # noqa: E402

RNG = np.random.default_rng(0)

# 発走時刻は JST の hhmm、fetched_at は UTC。JST = UTC + 9h。
QUERY = """
WITH r AS (
  SELECT id, date, course_name, race_number, head_count, post_time,
         (to_timestamp(date || post_time, 'YYYYMMDDHH24MI') - interval '9 hours') AS post_utc
  FROM chihou.races
  WHERE date BETWEEN %(start)s AND %(end)s AND course <> '83'
    AND post_time ~ '^[0-9]{4}$'
),
snap AS (
  SELECT DISTINCT ON (o.race_id, o.combination)
         o.race_id, o.combination::int AS hn, o.odds AS pre_odds, o.fetched_at
  FROM r
  JOIN chihou.odds_history o
    ON o.race_id = r.id AND o.bet_type = 'win'
   AND o.fetched_at <= r.post_utc - (%(lead)s || ' minutes')::interval
  ORDER BY o.race_id, o.combination, o.fetched_at DESC
),
pay AS (
  SELECT p.race_id, p.combination::int AS hn, p.payout / 100.0 AS place_ret
  FROM chihou.race_payouts p JOIN r ON r.id = p.race_id
  WHERE p.bet_type = 'place' AND p.combination ~ '^[0-9]+$'
)
SELECT r.id AS race_id, r.date, r.course_name, r.race_number, r.head_count, r.post_utc,
       rr.horse_number AS hn, snap.pre_odds, snap.fetched_at, rr.win_odds AS final_odds,
       rr.finish_position, COALESCE(pay.place_ret, 0.0) AS place_ret,
       (SELECT count(*) FROM chihou.race_payouts p2
         WHERE p2.race_id = r.id AND p2.bet_type = 'place') AS n_pay
FROM r
JOIN chihou.race_results rr ON rr.race_id = r.id
LEFT JOIN snap ON snap.race_id = r.id AND snap.hn = rr.horse_number
LEFT JOIN pay ON pay.race_id = r.id AND pay.hn = rr.horse_number
WHERE rr.finish_position IS NOT NULL AND COALESCE(rr.abnormality_code, 0) = 0
"""


def _ci(v: np.ndarray) -> tuple[float, float, float]:
    if len(v) == 0:
        return 0.0, 0.0, 0.0
    b = RNG.choice(v, size=(4000, len(v)), replace=True).mean(axis=1)
    return float(v.mean()), *np.percentile(b, [2.5, 97.5])


def _show(label: str, sub: pd.DataFrame, months: float) -> None:
    if len(sub) < 10:
        print(f"  {label:<44} n={len(sub):>4}  — 標本不足")
        return
    r, lo, hi = _ci(sub["place_ret"].values)
    hit = (sub["place_ret"] > 0).mean()
    print(f"  {label:<44} n={len(sub):>4} ({len(sub) / months * 12:>5,.0f}/年) "
          f"複勝率={hit:.3f} ROI={r:.3f} CI[{lo:.3f},{hi:.3f}]")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--lead", type=int, default=5, help="発走何分前のスナップショットを使うか")
    args = p.parse_args()

    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(QUERY, {"start": args.start, "end": args.end, "lead": str(args.lead)})
    df = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
    cur.close()
    conn.close()

    for col in ("pre_odds", "final_odds", "place_ret", "head_count", "finish_position", "n_pay"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df[df["n_pay"] > 0].copy()
    months = max((pd.to_datetime(args.end) - pd.to_datetime(args.start)).days / 30.4, 0.5)

    n_race_all = df["race_id"].nunique()
    df = df[df["pre_odds"].notna()].copy()
    print(f"対象 {args.start}〜{args.end} (発走{args.lead}分前スナップ): "
          f"{len(df):,}行 / {df['race_id'].nunique():,}レース"
          f"（オッズ取得できたレースのみ・全体 {n_race_all:,}）")

    # 発走前オッズだけで share を算出
    share = df.groupby("race_id")["pre_odds"].apply(chihou_market_top3_share)
    df["share"] = df["race_id"].map(share)
    df["drift"] = df["final_odds"] / df["pre_odds"]

    base = (df["head_count"] >= 8) & df["share"].notna()
    print(f"\n{'=' * 96}")
    print(f"  発走{args.lead}分前オッズだけで判定（＝本番で再現可能な唯一の条件）")
    print(f"{'=' * 96}")
    cur_cond = base & (df["pre_odds"] >= 30) & (df["pre_odds"] < 50) & (df["share"] < 0.63)
    _show("現行条件（30-50倍 & シェア<0.63）", df[cur_cond], months)

    print(f"\n  ── オッズ帯を動かす（シェア<0.63・8頭以上）──")
    for lo, hi in [(25, 45), (30, 45), (30, 50), (30, 60), (35, 55), (25, 50), (20, 50)]:
        _show(f"{lo}-{hi}倍", df[base & (df["pre_odds"] >= lo) & (df["pre_odds"] < hi)
                                & (df["share"] < 0.63)], months)

    print(f"\n  ── シェア閾値を動かす（30-50倍・8頭以上）──")
    band = base & (df["pre_odds"] >= 30) & (df["pre_odds"] < 50)
    for s in (0.70, 0.66, 0.63, 0.60, 0.57):
        _show(f"シェア<{s:.2f}", df[band & (df["share"] < s)], months)

    print(f"\n  ── 参考: 確定オッズで判定した場合（賭け時には不可能）──")
    fshare = df.groupby("race_id")["final_odds"].apply(chihou_market_top3_share)
    df["fshare"] = df["race_id"].map(fshare)
    _show("確定オッズ版 30-50倍 & シェア<0.63",
          df[(df["head_count"] >= 8) & (df["final_odds"] >= 30) & (df["final_odds"] < 50)
             & (df["fshare"] < 0.63)], months)

    print(f"\n{'=' * 96}")
    print("  オッズの流れ（drift = 確定 / 発走前）— 選ばれた馬がどれだけ流れるか")
    print(f"{'=' * 96}")
    sel = df[cur_cond]
    if len(sel):
        print(f"  選択馬の drift: 中央値={sel['drift'].median():.2f}  "
              f"75%点={sel['drift'].quantile(0.75):.2f}  95%点={sel['drift'].quantile(0.95):.2f}")
        print(f"  確定オッズが 50倍超へ流れた割合: {(sel['final_odds'] >= 50).mean() * 100:.1f}%")
        for lab, m in [("流れた馬(drift>=1.3)", sel["drift"] >= 1.3),
                       ("流れなかった馬(drift<1.3)", sel["drift"] < 1.3),
                       ("買われた馬(drift<1.0)", sel["drift"] < 1.0)]:
            _show(f"  {lab}", sel[m], months)


if __name__ == "__main__":
    main()
