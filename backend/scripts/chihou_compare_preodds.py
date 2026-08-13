"""P3: 地方指数の「オッズなし版」と「確定オッズ版」を直接比較する。

## 背景

`ChihouIndexCalculator.calculate_and_save(race_id, odds_map=None)` は odds_map が
None のとき `chihou.race_results.win_odds`（レース確定後にしか入らない値）を読む。
**コードベース全体で odds_map を渡している呼び出しは 1 つも無い**ため、
レース前の算出では市場5特徴が中立値に退化する:

  odds_rank_n = 0.5 固定 / is_heavy_fav = 0 / is_dark_horse = 0
  speed_mkt_gap → 0.5 - speed_rank_n / kc_mkt_gap → 0.5 - kc_rank_n

同じレースの v13 行は 1 日に 3 回書かれ、最後（当日 21:30 JST・全レース確定後）
だけが DB に残る。つまり:

  - **日中ユーザーに提示される値** = オッズなし版（消える）
  - **DB に残り過去分析に使われる値** = 確定オッズ版

この 2 つは別物であり、**オッズなし版の品質は一度も測られていない**。

## 使い方

1. レース当日の日中（21:30 JST より前）にオッズなし版を退避する:

       python scripts/chihou_compare_preodds.py snapshot --date 20260813 --out pre.csv

2. 翌日以降（21:30 の上書き後）に比較する:

       python scripts/chihou_compare_preodds.py compare --snapshot pre.csv

比較は「同一レース・同一モデル・同一サブ指数」で市場特徴の入力だけが違う対照実験になる。
退避時点で既に結果が出ていたレースは母集団から除外する（そこだけオッズが入っており
対照にならないため）。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_root.parent / ".env")

import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402

from src.indices.chihou_calculator import CHIHOU_COMPOSITE_VERSION  # noqa: E402

DSN = (
    f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
    f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
    f"password={os.getenv('DB_PASSWORD')}"
)

SNAPSHOT_QUERY = """
    SELECT r.id AS race_id, r.date, r.course, r.course_name, r.race_number,
           e.horse_number, ci.horse_id,
           ci.composite_index, ci.win_probability, ci.place_probability,
           ci.calculated_at
    FROM chihou.calculated_indices ci
    JOIN chihou.races r ON r.id = ci.race_id
    JOIN chihou.race_entries e ON e.race_id = ci.race_id AND e.horse_id = ci.horse_id
    WHERE ci.version = %(ver)s AND r.date = %(date)s AND r.course <> '83'
    ORDER BY r.id, e.horse_number
"""

# 退避時点で結果が出ていたレース（＝オッズが入っていた可能性がある）を特定する
RESULTED_BEFORE_QUERY = """
    SELECT DISTINCT s.race_id
    FROM chihou.race_results s
    JOIN chihou.races r ON r.id = s.race_id
    WHERE r.date = %(date)s AND s.created_at < %(cutoff)s
"""

OUTCOME_QUERY = """
    SELECT s.race_id, s.horse_number, s.finish_position, s.win_odds, s.win_popularity
    FROM chihou.race_results s
    JOIN chihou.races r ON r.id = s.race_id
    WHERE r.date = %(date)s AND r.course <> '83'
      AND COALESCE(s.abnormality_code, 0) = 0 AND s.finish_position IS NOT NULL
"""


def _read_sql(conn, sql: str, params: dict) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    cur.close()
    return df


def cmd_snapshot(args: argparse.Namespace) -> None:
    conn = psycopg2.connect(DSN)
    df = _read_sql(conn, SNAPSHOT_QUERY, {"ver": CHIHOU_COMPOSITE_VERSION, "date": args.date})
    conn.close()
    if df.empty:
        print(f"[!] {args.date} の v{CHIHOU_COMPOSITE_VERSION} が見つかりません")
        sys.exit(1)
    df.to_csv(args.out, index=False)
    print(f"退避: {len(df):,} 行 / {df['race_id'].nunique()} レース → {args.out}")
    print(f"  calculated_at(UTC) {df['calculated_at'].min()} 〜 {df['calculated_at'].max()}")
    print("  ※ 当日 21:30 JST の再算出より前に取ること（それ以降は確定オッズ版になる）")


def _top1(df: pd.DataFrame, score_col: str) -> pd.DataFrame:
    """レースごとの指数1位馬を返す。"""
    idx = df.groupby("race_id")[score_col].idxmax()
    return df.loc[idx, ["race_id", "horse_number", score_col]].rename(
        columns={"horse_number": "top1_hn"}
    )


def cmd_compare(args: argparse.Namespace) -> None:
    pre = pd.read_csv(args.snapshot)
    date = str(pre["date"].iloc[0])
    cutoff_ts = pd.to_datetime(pre["calculated_at"]).max()

    conn = psycopg2.connect(DSN)
    post = _read_sql(conn, SNAPSHOT_QUERY, {"ver": CHIHOU_COMPOSITE_VERSION, "date": date})
    # created_at は JST 保存。calculated_at は UTC 保存なので +9h して突き合わせる。
    resulted = _read_sql(
        conn, RESULTED_BEFORE_QUERY,
        {"date": date, "cutoff": cutoff_ts + pd.Timedelta(hours=9)},
    )
    outcome = _read_sql(conn, OUTCOME_QUERY, {"date": date})
    conn.close()

    if post.empty:
        print("[!] 現在の DB に該当データがありません")
        sys.exit(1)

    same = pd.to_datetime(post["calculated_at"]).max() == cutoff_ts
    if same:
        print("[!] DB がまだ上書きされていません（退避時と同じ calculated_at）。")
        print("    当日 21:30 JST の再算出を待ってから実行してください。")
        sys.exit(1)

    excluded = set(resulted["race_id"]) if not resulted.empty else set()
    pre = pre[~pre["race_id"].isin(excluded)]
    post = post[~post["race_id"].isin(excluded)]

    print("=" * 74)
    print(f"  地方指数 オッズなし版 vs 確定オッズ版  ({date} / v{CHIHOU_COMPOSITE_VERSION})")
    print("=" * 74)
    print(f"  退避時 calculated_at(UTC): {cutoff_ts}")
    print(f"  現在の calculated_at(UTC): {pd.to_datetime(post['calculated_at']).max()}")
    print(f"  除外（退避時に結果済み）: {len(excluded)} レース")
    print(f"  母集団: {post['race_id'].nunique()} レース / {len(post):,} 行")

    key = ["race_id", "horse_number"]
    m = pre.merge(post, on=key, suffixes=("_pre", "_post"))
    if m.empty:
        print("[!] 突合できる行がありません")
        sys.exit(1)

    # ---- 指数そのものの変化 ----
    d = (m["composite_index_post"] - m["composite_index_pre"]).abs()
    print("\n[指数の変化]")
    print(f"  |Δcomposite| 平均 {d.mean():.2f} / 中央 {d.median():.2f} / 最大 {d.max():.2f}")
    print(f"  1点も動かなかった行: {(d < 1e-9).mean()*100:.1f}%")

    # ---- レース内順位の一致 ----
    def _rank(df: pd.DataFrame, col: str) -> pd.Series:
        return df.groupby("race_id")[col].rank(ascending=False, method="min")

    m["rank_pre"] = _rank(m, "composite_index_pre")
    m["rank_post"] = _rank(m, "composite_index_post")
    spearman = m.groupby("race_id").apply(
        lambda g: g["rank_pre"].corr(g["rank_post"], method="spearman"), include_groups=False
    )
    print("\n[レース内順位の一致]")
    print(f"  Spearman 平均 {spearman.mean():.4f} / 中央 {spearman.median():.4f}")
    t_pre = _top1(m.rename(columns={"composite_index_pre": "s"}), "s")
    t_post = _top1(m.rename(columns={"composite_index_post": "s"}), "s")
    t = t_pre.merge(t_post, on="race_id", suffixes=("_pre", "_post"))
    agree = (t["top1_hn_pre"] == t["top1_hn_post"]).mean()
    print(f"  指数1位が一致したレース: {agree*100:.1f}%  ({int((t['top1_hn_pre']==t['top1_hn_post']).sum())}/{len(t)})")

    # ---- 実結果に対する的中率 ----
    if outcome.empty:
        print("\n[的中率] 結果未確定のため計算不可")
        return
    outcome = outcome[~outcome["race_id"].isin(excluded)]
    print("\n[実結果に対する 指数1位馬の成績]")
    for label, col in [("オッズなし版", "top1_hn_pre"), ("確定オッズ版", "top1_hn_post")]:
        j = t[["race_id", col]].merge(
            outcome, left_on=["race_id", col], right_on=["race_id", "horse_number"], how="inner"
        )
        if j.empty:
            print(f"  {label}: 突合できず")
            continue
        win = (j["finish_position"] == 1).mean()
        top3 = (j["finish_position"] <= 3).mean()
        roi = j.loc[j["finish_position"] == 1, "win_odds"].sum() / len(j)
        print(
            f"  {label}: n={len(j)}  勝率 {win*100:5.1f}%  複勝率 {top3*100:5.1f}%  単勝ROI {roi:.3f}"
        )
    print("\n  ※ 1日分は標本が小さい。傾向を見るだけで結論は出さないこと。")
    print("     複数日ぶん退避してから判断する。")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot", help="オッズなし版を CSV へ退避する（当日 21:30 JST より前）")
    s.add_argument("--date", required=True, help="対象日 YYYYMMDD")
    s.add_argument("--out", required=True, help="出力 CSV パス")
    s.set_defaults(func=cmd_snapshot)

    c = sub.add_parser("compare", help="退避済み CSV と現在の DB を比較する")
    c.add_argument("--snapshot", required=True, help="snapshot で作った CSV")
    c.set_defaults(func=cmd_compare)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
