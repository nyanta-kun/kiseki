"""地方競馬 sweet_spot / place_bet バックテスト（本番と同一母集団で honest 計算）。

2026-07-23 監査で判明した生存者バイアスの修正版:
  旧来のバックテスト系スクリプトは race_results を INNER JOIN し
  「完走・正常決着馬のみ」で idx_rank（指数順位）を再計算していたため、
  本番の指数1位馬が出走取消/失格になると2位馬が繰り上がって1位扱いになる
  生存者バイアスを含んでいた（本番 chihou_recommender.rank_by_hn は
  出走予定馬全体で順位を確定させるため、この乖離は起きない）。

  本スクリプトは出走予定馬全体（LEFT JOIN）で idx_rank を計算してから
  判定・集計を確定結果のみに絞り込むことで、本番と同一の母集団定義を保つ。
  --show-bias で旧ロジック（バイアスあり）との比較も表示できる。

使い方:
  cd backend
  .venv/bin/python scripts/backtest_chihou_sweetspot.py --version 10
  .venv/bin/python scripts/backtest_chihou_sweetspot.py --version 10 --show-bias
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv
load_dotenv(_root.parent / ".env")

import pandas as pd
import psycopg2

from src.indices.buy_signal import chihou_is_place_bet, chihou_is_sweet_spot

DSN = (
    f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
    f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
    f"password={os.getenv('DB_PASSWORD')}"
)

SQL = """
SELECT
    r.id   AS race_id,
    r.date,
    r.course_name,
    r.head_count,
    re.horse_number,
    ci.composite_index::float       AS composite_index,
    rr.win_odds::float              AS win_odds,
    rr.place_odds::float            AS place_odds,
    rr.finish_position,
    COALESCE(rr.abnormality_code, 0) AS abnormality_code
FROM chihou.calculated_indices ci
JOIN chihou.races r ON r.id = ci.race_id
JOIN chihou.race_entries re
    ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
LEFT JOIN chihou.race_results rr
    ON rr.race_id = ci.race_id AND rr.horse_number = re.horse_number
WHERE ci.version = %s
  AND r.course != '83'
  AND r.head_count >= 6
  AND r.date >= %s
  AND r.date <= %s
ORDER BY r.date, r.id, re.horse_number
"""


def _stats(sub: pd.DataFrame, bet: str) -> tuple[int, int, float]:
    if sub.empty:
        return 0, 0, 0.0
    if bet == "win":
        mask = sub["finish_position"] == 1
        n = len(sub)
        hits = int(mask.sum())
        roi = float(sub.loc[mask, "win_odds"].sum()) / n if n else 0.0
    else:
        valid = sub[sub["place_odds"].notna()]
        n = len(valid)
        mask = valid["finish_position"].between(1, 3, inclusive="both")
        hits = int(mask.sum())
        roi = float(valid.loc[mask, "place_odds"].sum()) / n if n else 0.0
    return n, hits, roi


def _show(label: str, sub: pd.DataFrame, bet: str) -> None:
    n, hits, roi = _stats(sub, bet)
    hr = hits / n * 100 if n else 0.0
    tag = "単勝ROI" if bet == "win" else "複勝ROI"
    print(f"  {label:<20} n={n:>5,}  hits={hits:>4}  hit_rate={hr:5.1f}%  {tag}={roi:.3f}")


def _categorize(settled: pd.DataFrame, rank_col: str, fav_col: str) -> dict[str, pd.DataFrame]:
    ss = settled[
        settled.apply(
            lambda x: chihou_is_sweet_spot(
                int(x[rank_col]) if pd.notna(x[rank_col]) else None, x["win_odds"], x["course_name"]
            ),
            axis=1,
        )
    ].copy()
    ss_k = ss.groupby("race_id").size()
    ss = ss[ss["race_id"].isin(ss_k[ss_k <= 2].index)]

    pb = settled[
        settled.apply(
            lambda x: chihou_is_place_bet(
                int(x[rank_col]) if pd.notna(x[rank_col]) else None, x["win_odds"], x[fav_col],
                int(x["head_count"]) if pd.notna(x["head_count"]) else None,
            ),
            axis=1,
        )
    ].copy()
    pb_k = pb.groupby("race_id").size()
    pb = pb[pb["race_id"].isin(pb_k[pb_k <= 2].index)]
    return {"sweet_spot": ss, "place_bet": pb}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--version", type=int, required=True, help="chihou.calculated_indices の version")
    p.add_argument("--start", default="20000101")
    p.add_argument("--end", default="30000101")
    p.add_argument("--show-bias", action="store_true", help="旧ロジック(生存者バイアスあり)との比較も表示")
    args = p.parse_args()

    print(f"DB接続中... version={args.version} 期間 {args.start}〜{args.end}")
    conn = psycopg2.connect(DSN)
    df = pd.read_sql(SQL, conn, params=(args.version, args.start, args.end))
    conn.close()
    df["date"] = df["date"].astype(str)
    print(f"取得: {len(df):,}行 ({df['date'].min()}〜{df['date'].max()})")

    # 本番と同一母集団（出走予定馬全体）で idx_rank を確定
    df["idx_rank_fixed"] = (
        df.groupby("race_id")["composite_index"].rank(method="first", ascending=False).astype("Int64")
    )
    df["fav_odds"] = df.groupby("race_id")["win_odds"].transform("min")

    settled = df[
        df["finish_position"].notna()
        & (df["abnormality_code"] == 0)
        & df["win_odds"].notna()
        & (df["win_odds"] >= 1.0)
    ].copy()

    print(f"\n{'='*70}\n  honest（本番同一母集団で idx_rank）\n{'='*70}")
    cats = _categorize(settled, "idx_rank_fixed", "fav_odds")
    _show("sweet_spot", cats["sweet_spot"], "win")
    _show("place_bet", cats["place_bet"], "place")

    if args.show_bias:
        biased_pop = settled.copy()
        biased_pop["idx_rank_biased"] = (
            biased_pop.groupby("race_id")["composite_index"].rank(method="first", ascending=False).astype("Int64")
        )
        print(f"\n{'='*70}\n  旧ロジック（完走馬のみで idx_rank 再計算＝生存者バイアスあり・参考比較用）\n{'='*70}")
        cats_biased = _categorize(biased_pop, "idx_rank_biased", "fav_odds")
        _show("sweet_spot", cats_biased["sweet_spot"], "win")
        _show("place_bet", cats_biased["place_bet"], "place")


if __name__ == "__main__":
    main()
