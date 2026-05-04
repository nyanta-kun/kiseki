"""地方競馬 直近30日 sweet_spot / place_bet / low_odds の実勢集計

API レスポンスの summaries と同等のロジックを DB 直接で計算する。

使い方:
  cd backend
  .venv/bin/python scripts/aggregate_chihou_recent.py [--days 30]
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv
load_dotenv(_root.parent / ".env")

import pandas as pd
import psycopg2

DSN = (
    f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
    f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
    f"password={os.getenv('DB_PASSWORD')}"
)

# 推奨ロジックと同じ閾値
SWEET_SPOT_COURSES = {"浦和", "水沢", "笠松", "園田", "佐賀", "高知", "姫路", "盛岡", "門別"}
SS_MIN_ODDS = 10.0
SS_MIN_EV = 1.0
SS_MAX_EV = 2.0

PB_FAV_ODDS_MAX = 2.0  # 1番人気 < 2.0
PB_MIN_EV = 1.2
PB_MAX_EV = 2.0

LOW_ODDS_TRUSTED_MAX = 1.5  # 単勝 < 1.5
LOW_ODDS_UNTRUSTED_MAX = 2.0  # 1.5 ≤ 単勝 < 2.0


SQL = """
SELECT
    r.id   AS race_id,
    r.date,
    r.course_name,
    re.horse_number,
    ci.win_probability::float       AS win_probability,
    ci.place_probability::float     AS place_probability,
    rr.win_odds::float              AS win_odds,
    rr.place_odds::float            AS place_odds,
    rr.finish_position
FROM chihou.calculated_indices ci
JOIN chihou.races r ON r.id = ci.race_id
JOIN chihou.race_entries re
    ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
JOIN chihou.race_results rr
    ON rr.race_id = ci.race_id AND rr.horse_number = re.horse_number
WHERE ci.version = 10
  AND r.course != '83'
  AND r.head_count >= 6
  AND r.date >= %s
  AND r.date <= %s
  AND rr.finish_position IS NOT NULL
  AND rr.win_odds IS NOT NULL
  AND rr.win_odds::float >= 1.0
  AND COALESCE(rr.abnormality_code, 0) = 0
ORDER BY r.date, r.id, re.horse_number
"""


def aggregate_period(df: pd.DataFrame, label: str) -> None:
    print(f"\n{'='*70}")
    print(f"  {label}  ({df['date'].min()} 〜 {df['date'].max()}, レース数 {df.groupby(['date','race_id']).ngroups:,})")
    print('='*70)
    df = df.copy()
    df["ev"] = df["win_probability"] * df["win_odds"]

    # ---------- sweet_spot: 単勝>=10 ∧ EV1.0-2.0 ∧ 9場 ∧ k<=2 (sweet該当馬数) ----------
    ss_mask = (
        (df["win_odds"] >= SS_MIN_ODDS)
        & (df["ev"] >= SS_MIN_EV)
        & (df["ev"] <= SS_MAX_EV)
        & (df["course_name"].isin(SWEET_SPOT_COURSES))
    )
    ss = df[ss_mask].copy()
    # k<=2 制約: レース内 sweet 該当馬数 が 1〜2 のみ採用
    ss_k = ss.groupby("race_id").size()
    ss = ss[ss["race_id"].isin(ss_k[ss_k <= 2].index)]
    print_block("sweet_spot (高オッズ穴)", ss, bet="win")

    # ---------- low_odds_trusted: 単勝<1.5、最低オッズ馬1頭/レース ----------
    lo_t = df[df["win_odds"] < LOW_ODDS_TRUSTED_MAX]
    lo_t = lo_t.sort_values(["race_id", "win_odds"]).drop_duplicates("race_id", keep="first")
    print_block("low_odds_trusted (単勝<1.5)", lo_t, bet="win")

    # ---------- low_odds_untrusted: 1.5<=単勝<2.0、最低オッズ馬1頭/レース ----------
    lo_u = df[(df["win_odds"] >= LOW_ODDS_TRUSTED_MAX) & (df["win_odds"] < LOW_ODDS_UNTRUSTED_MAX)]
    lo_u = lo_u.sort_values(["race_id", "win_odds"]).drop_duplicates("race_id", keep="first")
    print_block("low_odds_untrusted (1.5<=単勝<2.0)", lo_u, bet="win")

    # ---------- place_bet (複穴): 1番人気<2.0 ∧ 単勝>=10 ∧ EV1.2-2.0、複勝買い ----------
    fav = df.sort_values(["race_id", "win_odds"]).groupby("race_id").first()["win_odds"]
    target_races = fav[fav < PB_FAV_ODDS_MAX].index
    pb = df[
        (df["race_id"].isin(target_races))
        & (df["win_odds"] >= SS_MIN_ODDS)
        & (df["ev"] >= PB_MIN_EV)
        & (df["ev"] <= PB_MAX_EV)
    ]
    print_block("place_bet (複穴: 断然人気R × EV1.2-2.0 × 複勝)", pb, bet="place")


def print_block(label: str, sub: pd.DataFrame, bet: str) -> None:
    if sub.empty:
        print(f"\n  [{label}] 該当なし")
        return

    if bet == "win":
        win_mask = sub["finish_position"] == 1
        hits = int(win_mask.sum())
        n = len(sub)
        roi = float(sub.loc[win_mask, "win_odds"].sum()) / n if n else 0.0
        avg_odds = float(sub["win_odds"].mean())
        print(f"\n  [{label}] win_bet")
        print(f"    n={n:,}  hits={hits}  hit_rate={hits/n*100:.1f}%  単勝ROI={roi:.3f}  avg単勝={avg_odds:.1f}")
    else:  # place
        place_mask = sub["finish_position"].between(1, 3, inclusive="both")
        hits = int(place_mask.sum())
        n = len(sub)
        # 複勝オッズが NULL ならスキップ
        valid_place = sub[sub["place_odds"].notna()]
        if not valid_place.empty:
            vp_hits = valid_place["finish_position"].between(1, 3, inclusive="both")
            roi = float(valid_place.loc[vp_hits, "place_odds"].sum()) / len(valid_place)
            print(f"\n  [{label}] place_bet")
            print(f"    n={n:,}  hits={hits}  hit_rate={hits/n*100:.1f}%  複勝ROI={roi:.3f}  (place_odds有 n={len(valid_place):,})")
        else:
            print(f"\n  [{label}] place_bet")
            print(f"    n={n:,}  hits={hits}  hit_rate={hits/n*100:.1f}%  (place_odds データなし)")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=30)
    args = p.parse_args()

    today = date.today()
    start = today - timedelta(days=args.days)

    print(f"DB接続中... 期間: {start.strftime('%Y%m%d')} 〜 {today.strftime('%Y%m%d')}")
    conn = psycopg2.connect(DSN)
    df = pd.read_sql(SQL, conn, params=(start.strftime("%Y%m%d"), today.strftime("%Y%m%d")))
    conn.close()

    print(f"取得: {len(df):,}馬 / レース {df.groupby(['date','race_id']).ngroups:,}")
    if df.empty:
        return

    df["date"] = df["date"].astype(str)

    # 全期間
    aggregate_period(df, f"直近{args.days}日")
    # 直近7日も
    cutoff = (today - timedelta(days=7)).strftime("%Y%m%d")
    aggregate_period(df[df["date"] >= cutoff], "直近 7日")
    # 直近1日
    cutoff = (today - timedelta(days=1)).strftime("%Y%m%d")
    last1 = df[df["date"] >= cutoff]
    if not last1.empty:
        aggregate_period(last1, "直近 1日")


if __name__ == "__main__":
    main()
