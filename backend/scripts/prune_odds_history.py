"""`keiba.odds_history` を刈り込む（13GB / 60.4M 行・年 約18GB 増）。

台帳 `docs/jra_rebuild_2026_08.md` 課題#11。

## 実測した内訳（2026-08 の 4 開催日・9,878,382 行）

| 帯 | 行数 | 割合 | うち win/place |
|---|---|---|---|
| 発走前 0〜60分 | 4,273,706 | 43.3% | 296,632（**7%**） |
| 発走前 60〜180分 | 586,398 | 5.9% | 100% |
| 発走前 180〜360分 | 618,214 | 6.3% | 100% |
| 発走前 360分超 | 460,134 | 4.7% | 100% |
| **発走後** | **3,939,930** | **39.9%** | 84% |

読み取れること:

- **発走後が 4 割。** realtime は約30秒ごとに全レースキーを叩き続けるので、
  終わったレースの確定オッズを延々と書き足している。**何も読んでいない**
  （確定オッズは `race_results.win_odds` にある）
- **発走前 60分以内の 93% は 3連単等の exotic。** exotic は締切間際にしか取得されず、
  組み合わせ数が多いので行数を支配する
- **60分より前は 100% が win/place。** 長い時系列を使う分析
  （`jra_phase4a_odds_movement_analysis.py` の early/late 比）はこの帯に依存する

## 既定の方針

1. `post` — **発走後の行を削除**（−約40%）。読み手が無い。
   むしろ `jra_phase4a` の「late 20%点」が発走後の値で汚染されているのを直す副作用がある
2. `exotic` — **exotic 券種は発走前の最終スナップショットだけ残す**（−約40%）。
   ただし **`--exotic-keep-days`（既定 21 日）より新しいレースは潰さない**。

   🔴 **2026-08-23 に前提が変わった。** かつては「exotic 時系列を読むスクリプトは1本も無い」
   ことがこの方針の根拠だった。#265 で exotic オッズのパースが直り、
   三連複オッズから逆算した「3着以内」確率が単勝プール由来のものより
   実際の着順をよく当てることが分かった（穴馬帯の AUC 増分 +0.0066）。
   この発見を実運用に載せられるかは **「発走 N 分前のオッズでも同じ差が出るか」** に懸かっており、
   その検定には**発走前 30 分の時系列そのもの**が要る。最終スナップショットだけでは測れない。

   三連単だけは保持期間内でも潰す。JVDF が上位500人気で打ち切って配信するため
   分母が閉じず（Σ(1/odds) から逆算した払戻率が 81.6%・公式 72.5%）、
   期待値計算に使えないことが実測で確定している。しかも exotic 行数の約45%を占める。

⚠️ **`win` / `place` の発走前時系列には触らない。** 前向き記録（発走10分前）・
`jra_odds_cross_bettype_arbitrage`・`jra_phase4a` が使う。ここを削ると
「発走前オッズを使う検証は 2026-03-28 以降のみ」という既に狭い窓がさらに狭くなる。

⚠️ **削除は元に戻せない。** 実行前に当日の DB バックアップ（03:30 JST）を確認すること。

使い方:
    cd backend
    .venv/bin/python scripts/prune_odds_history.py                        # dry-run
    .venv/bin/python scripts/prune_odds_history.py --policy post --execute
    .venv/bin/python scripts/prune_odds_history.py --policy post,exotic --execute
    .venv/bin/python scripts/prune_odds_history.py --before 20260701      # 対象期間を限る
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import psycopg2  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("prune_oh")

KEEP_BET_TYPES = ("win", "place")

# exotic とみなす券種（= 最終スナップショット以外を潰す対象）
EXOTIC_BET_TYPES = ("bracket", "quinella", "wide", "exacta", "trio", "trifecta")
# 保持期間内でも潰す券種（上位500人気で打ち切られており期待値計算に使えない）
ALWAYS_COLLAPSE = ("trifecta",)

# win/place の発走前時系列には絶対に触らない（前向き記録・odds 系分析が使う）
assert not set(KEEP_BET_TYPES) & set(EXOTIC_BET_TYPES)
assert set(ALWAYS_COLLAPSE) <= set(EXOTIC_BET_TYPES)

# ⚠️ `fetched_at` は UTC・`races.post_time` は JST。9時間ずらして比較する（台帳 14.2）
#
# 🔴 **日付ごとに回すこと。** 全期間を1本の CTE でやると `fetched_at > post_utc` を
# 支える索引が無く、バッチのたびに 6,000万行を全走査する（実測で1バッチが返らない）。
# `race_id` には索引があるので、対象レースを絞ってから条件を当てる形にする。
DATES_SQL = """
SELECT DISTINCT date FROM keiba.races
WHERE post_time ~ '^[0-9]{4}$' AND date >= %(start)s AND date < %(before)s
ORDER BY date
"""

RACES_SQL = """
SELECT id, to_timestamp(date || post_time, 'YYYYMMDDHH24MI') - interval '9 hours'
FROM keiba.races
WHERE date = %(date)s AND post_time ~ '^[0-9]{4}$'
"""

COUNT_SQL = {
    # 発走後に書かれた行（realtime の空回り）
    "post": """
    SELECT count(*) FROM keiba.odds_history
    WHERE race_id = %(race_id)s AND fetched_at > %(post_utc)s
    """,
    # exotic 券種の「発走前・最終スナップショット以外」
    "exotic": """
    WITH latest AS (
      SELECT bet_type, combination, max(fetched_at) AS last_at
      FROM keiba.odds_history
      WHERE race_id = %(race_id)s AND bet_type IN %(collapse)s
        AND fetched_at <= %(post_utc)s
      GROUP BY 1,2
    )
    SELECT count(*) FROM keiba.odds_history o
    JOIN latest l ON l.bet_type = o.bet_type AND l.combination = o.combination
    WHERE o.race_id = %(race_id)s AND o.bet_type IN %(collapse)s
      AND o.fetched_at <= %(post_utc)s AND o.fetched_at < l.last_at
    """,
}

DELETE_SQL = {
    "post": """
    DELETE FROM keiba.odds_history
    WHERE race_id = %(race_id)s AND fetched_at > %(post_utc)s
    """,
    "exotic": """
    WITH latest AS (
      SELECT bet_type, combination, max(fetched_at) AS last_at
      FROM keiba.odds_history
      WHERE race_id = %(race_id)s AND bet_type IN %(collapse)s
        AND fetched_at <= %(post_utc)s
      GROUP BY 1,2
    ), doomed AS (
      SELECT o.ctid FROM keiba.odds_history o
      JOIN latest l ON l.bet_type = o.bet_type AND l.combination = o.combination
      WHERE o.race_id = %(race_id)s AND o.bet_type IN %(collapse)s
        AND o.fetched_at <= %(post_utc)s AND o.fetched_at < l.last_at
    )
    DELETE FROM keiba.odds_history WHERE ctid IN (SELECT ctid FROM doomed)
    """,
}


def connect():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--policy", default="post,exotic",
                   help="post（発走後）/ exotic（exotic の最終以外）をカンマ区切りで")
    p.add_argument("--start", default="20000101")
    p.add_argument("--before", default=None,
                   help="この日より前を対象にする（既定は今日。当日分は触らない）")
    p.add_argument("--execute", action="store_true", help="付けないと dry-run")
    p.add_argument("--sample-dates", type=int, default=4,
                   help="dry-run で見積りに使う開催日数（全期間を数えると重いため）")
    p.add_argument("--exotic-keep-days", type=int, default=21,
                   help="この日数より新しいレースは exotic 時系列を潰さない"
                        "（発走前オッズの検証に使う。三連単は対象外で常に潰す）")
    p.add_argument("--sleep", type=float, default=0.3, help="日ごとの休止秒")
    args = p.parse_args()

    import datetime
    before = args.before or datetime.date.today().strftime("%Y%m%d")
    policies = [x.strip() for x in args.policy.split(",") if x.strip()]
    for pol in policies:
        if pol not in COUNT_SQL:
            raise SystemExit(f"未知の policy: {pol}")

    keep_cutoff = (
        datetime.datetime.strptime(before, "%Y%m%d").date()
        - datetime.timedelta(days=args.exotic_keep_days)
    ).strftime("%Y%m%d")

    def _collapse_for(date: str) -> tuple[str, ...]:
        """その開催日で「最終スナップショット以外を潰す」券種。"""
        if date >= keep_cutoff:
            return ALWAYS_COLLAPSE
        return EXOTIC_BET_TYPES

    conn = connect()
    cur = conn.cursor()
    cur.execute(DATES_SQL, {"start": args.start, "before": before})
    dates = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT count(*) FROM keiba.odds_history")
    total = cur.fetchone()[0]
    logger.info(f"対象期間 {args.start}〜{before}（当日は触らない）/ 開催日 {len(dates)}")
    logger.info(f"exotic 時系列の保持: {keep_cutoff} 以降のレースは潰さない"
                f"（--exotic-keep-days={args.exotic_keep_days}、"
                f"ただし {'/'.join(ALWAYS_COLLAPSE)} は常に潰す）")
    logger.info(f"現在の総行数 {total:,}")
    if not dates:
        return

    def _races(date: str) -> list[tuple[int, object]]:
        cur.execute(RACES_SQL, {"date": date})
        return cur.fetchall()

    if not args.execute:
        # 全期間を数えると重いので、直近の数開催日から比率で見積もる
        sample = dates[-args.sample_dates:]
        logger.info(f"dry-run: 直近 {len(sample)} 開催日 ({sample[0]}〜{sample[-1]}) で見積り")
        sampled_total = 0
        planned = {pol: 0 for pol in policies}
        for date in sample:
            for race_id, post_utc in _races(date):
                cur.execute(
                    "SELECT count(*) FROM keiba.odds_history WHERE race_id = %s", (race_id,)
                )
                sampled_total += cur.fetchone()[0]
                for pol in policies:
                    cur.execute(COUNT_SQL[pol], {
                        "race_id": race_id, "post_utc": post_utc,
                        "collapse": _collapse_for(date),
                    })
                    planned[pol] += cur.fetchone()[0]
        logger.info(f"標本の総行数 {sampled_total:,}")
        for pol in policies:
            pct = 100.0 * planned[pol] / sampled_total if sampled_total else 0
            logger.info(f"  policy={pol:<7} {planned[pol]:>10,} 行 ({pct:.1f}%) "
                        f"→ 全期間換算 約 {int(total * pct / 100):,} 行")
        s_all = sum(planned.values())
        pct_all = 100.0 * s_all / sampled_total if sampled_total else 0
        logger.info(f"合計 {pct_all:.1f}% → 全期間換算 約 {int(total * pct_all / 100):,} 行削減")
        logger.info("**実行前に当日の DB バックアップを確認すること**（--execute で実行）")
        return

    deleted = {pol: 0 for pol in policies}
    for i, date in enumerate(dates, 1):
        for race_id, post_utc in _races(date):
            for pol in policies:
                cur.execute(DELETE_SQL[pol], {
                    "race_id": race_id, "post_utc": post_utc,
                    "collapse": _collapse_for(date),
                })
                deleted[pol] += cur.rowcount
        conn.commit()   # 開催日ごとにコミット（長大トランザクションを作らない）
        if i % 10 == 0 or i == len(dates):
            logger.info(f"  [{i}/{len(dates)}] {date} まで削除 "
                        + " / ".join(f"{k}={v:,}" for k, v in deleted.items()))
        if args.sleep:
            time.sleep(args.sleep)
    logger.info("削除完了 " + " / ".join(f"{k}={v:,}" for k, v in deleted.items()))
    logger.info("VACUUM は autovacuum に任せる（VACUUM FULL は排他ロックを取る）")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
