"""地方競馬 直近30日 sweet_spot / place_bet / low_odds の実勢集計

本番配信ロジック（src/indices/buy_signal.py の Phase2 ランキング規則）を
そのまま import して DB 直接で実勢を計算する。
条件をこのスクリプト内に複製しないことで、本番との乖離（旧: EVゲート9場の
Phase1 条件が残存し、配信中の推奨と別母集団を集計していた）を防ぐ。

注意:
  - win_odds/place_odds は race_results の確定オッズを使用する。
    本番配信は発走前オッズ判定のため、境界近傍で若干の差異が出うる。

使い方:
  cd backend
  .venv/bin/python scripts/aggregate_chihou_recent.py [--days 30]
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv
load_dotenv(_root.parent / ".env")

import pandas as pd
import psycopg2

from src.indices.buy_signal import (
    chihou_is_place_bet,
    chihou_is_sweet_spot,
    chihou_low_odds_trust_level,
)
from src.indices.chihou_calculator import CHIHOU_COMPOSITE_VERSION

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
    re.horse_number,
    ci.composite_index::float       AS composite_index,
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
WHERE ci.version = %s
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


def _apply_production_rules(df: pd.DataFrame) -> pd.DataFrame:
    """本番の chihou_recommender と同じ順位・カテゴリ判定を各行に付与する。"""
    df = df.copy()
    # composite_index でレース内順位（降順・同値は先着）— recommender の rank_by_hn と同一
    df["idx_rank"] = (
        df.groupby("race_id")["composite_index"]
        .rank(method="first", ascending=False)
        .astype("Int64")
    )
    # 1番人気オッズ（レース内最低単勝）
    df["fav_odds"] = df.groupby("race_id")["win_odds"].transform("min")

    df["is_sweet_spot"] = df.apply(
        lambda x: chihou_is_sweet_spot(
            int(x["idx_rank"]) if pd.notna(x["idx_rank"]) else None,
            x["win_odds"],
            x["course_name"],
        ),
        axis=1,
    )
    df["is_place_bet"] = df.apply(
        lambda x: chihou_is_place_bet(
            int(x["idx_rank"]) if pd.notna(x["idx_rank"]) else None,
            x["win_odds"],
            x["fav_odds"],
        ),
        axis=1,
    )
    df["low_odds_level"] = df["win_odds"].map(chihou_low_odds_trust_level)
    return df


def aggregate_period(df: pd.DataFrame, label: str) -> None:
    print(f"\n{'='*70}")
    print(f"  {label}  ({df['date'].min()} 〜 {df['date'].max()}, レース数 {df.groupby(['date','race_id']).ngroups:,})")
    print('='*70)

    # ---------- sweet_spot (Phase2): 指数1位 ∧ 単勝10-30倍 ∧ 割安5場 ∧ k<=2 ----------
    ss = df[df["is_sweet_spot"]].copy()
    ss_k = ss.groupby("race_id").size()
    ss = ss[ss["race_id"].isin(ss_k[ss_k <= 2].index)]
    print_block("sweet_spot (Phase2: 指数1位×10-30倍×割安5場)", ss, bet="win")

    # ---------- low_odds_trusted: 単勝<1.5、最低オッズ馬1頭/レース ----------
    lo_t = df[df["low_odds_level"] == "trusted"]
    lo_t = lo_t.sort_values(["race_id", "win_odds"]).drop_duplicates("race_id", keep="first")
    print_block("low_odds_trusted (単勝<1.5)", lo_t, bet="win")

    # ---------- low_odds_untrusted: 1.5<=単勝<2.0、最低オッズ馬1頭/レース ----------
    lo_u = df[df["low_odds_level"] == "untrusted"]
    lo_u = lo_u.sort_values(["race_id", "win_odds"]).drop_duplicates("race_id", keep="first")
    print_block("low_odds_untrusted (1.5<=単勝<2.0)", lo_u, bet="win")

    # ---------- place_bet (Phase2): 断然人気R × 単勝>=10 × 指数3位以内 ∧ k<=2 ----------
    pb = df[df["is_place_bet"]].copy()
    pb_k = pb.groupby("race_id").size()
    pb = pb[pb["race_id"].isin(pb_k[pb_k <= 2].index)]
    print_block("place_bet (Phase2: 断然人気R×単勝>=10×指数3位以内)", pb, bet="place")


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

    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    start = today - timedelta(days=args.days)

    print(f"DB接続中... 期間: {start.strftime('%Y%m%d')} 〜 {today.strftime('%Y%m%d')} (version={CHIHOU_COMPOSITE_VERSION})")
    conn = psycopg2.connect(DSN)
    df = pd.read_sql(
        SQL,
        conn,
        params=(CHIHOU_COMPOSITE_VERSION, start.strftime("%Y%m%d"), today.strftime("%Y%m%d")),
    )
    conn.close()

    print(f"取得: {len(df):,}馬 / レース {df.groupby(['date','race_id']).ngroups:,}")
    if df.empty:
        return

    df["date"] = df["date"].astype(str)
    df = _apply_production_rules(df)

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
