#!/usr/bin/env python3
"""keiba / chihou のレースを sekito.kaisai + sekito.races へ供給する（CLI）。

sekito の `bin/run/sync-jra-from-jvlink`（旧 id=76）と
`bin/run/sync-nar-from-umaconn`（旧 id=88）の移設先。統合 Phase 5 の 5b-3。

## 🔴 なぜ要るか — 「不要」と判断して止めたら本番が止まった

統合 Phase 2（2026-09-07）で「供給同期は**不要**（kiseki は keiba.races /
chihou.races を直読みする）」と判断してこの 2 ジョブを無効化した。
これは **kiseki 側のスクレイパについてだけ**正しく、**sekito 自身の消費者**を
見落としていた。

2026-09-09 の実測:

    sekito.races  9/8 まで有り → **9/9・9/10・9/11 が 0 件**
                  （chihou.races には 44 / 47 / 35 件ある）
    sekito の /api/races?date=2026-09-09  → {"venues":[]}
    POG の通知は `FROM v_entries JOIN races r` で sekito.races を見ている

つまり **sekito のサイトと POG の出走通知が、今日から静かに空になっていた**。
`sekito.v_races` も実体は `sekito.races` のビューなので同じ穴に落ちる
（CLAUDE.md の「v_races は keiba.races のビュー」という記述は誤り）。

移設元の docstring 自身が同じ事故を記録している——2026-05-10 以降この 2 テーブルが
凍結してスクレイパが毎日「対象レースがありません」で 0 件サイレント終了していた、と。
**同じ穴を 4 か月後にもう一度掘ったことになる。**

## 🔴 ON CONFLICT を DO NOTHING にしてはいけない（JRA 側）

`--to` の既定が 2099-12-31 なので、**発走時刻が確定する前の未来のレースを先に
INSERT する**。その時点では `keiba.races.post_time` が NULL で `start_time` は
00:00 で入る。DO NOTHING だと前日に post_time が確定しても二度と更新されず、
`start_time` は 00:00 のまま固定される。

そうなると `netkeiba-paddock` の「発走20分以内」条件に永久に一致せず、
パドック評価が丸ごと取れなくなる（2026-09-05 に実際に踏んだ回帰）。

更新は **start_time だけ**に絞る。status / weather / condition / score1 等は
他のスクリプトが持ち主なので触らない。さらに「新しい値が 00:00 でない」ことを
WHERE で確認する——さもないと未来レースの同期が確定済みの発走時刻を 00:00 で
上書きし返す。

## 使い方

    # 既定: 過去7日〜将来全件
    docker exec -w /app galloplab-backend-1 /app/.venv/bin/python \
        scripts/sync_sekito_races.py

    # 欠損の埋め戻し / 片方だけ / 件数確認
    ... scripts/sync_sekito_races.py --from 2026-09-09 --to 2026-09-11
    ... scripts/sync_sekito_races.py --only nar
    ... scripts/sync_sekito_races.py --dry-run

終了コード: 0 = 正常 / 1 = 例外。
"""

from __future__ import annotations

import argparse
import logging
import sys
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import text

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.db.session import SyncSessionLocal  # noqa: E402
from src.utils.cron_run import RunRecord, record  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")

# JRA の場コード（JV-Link）。10 場で固定。
JRA_COURSES = "ARRAY['01','02','03','04','05','06','07','08','09','10']"

KAISAI_SQL = text(
    f"""
    INSERT INTO sekito.kaisai (date, course_code, kai, day)
    SELECT DISTINCT
        keiba.to_date_imm(r.date::text, 'YYYYMMDD'),
        sekito.jvlink_to_sekito_course(r.course::text),
        substring(r.jravan_race_id from 11 for 2)::int,
        substring(r.jravan_race_id from 13 for 2)::int
    FROM keiba.races r
    WHERE r.course = ANY({JRA_COURSES})
      AND r.jravan_race_id IS NOT NULL
      AND keiba.to_date_imm(r.date::text, 'YYYYMMDD') BETWEEN :from_date AND :to_date
    ON CONFLICT (date, course_code) DO UPDATE
        SET kai = EXCLUDED.kai, day = EXCLUDED.day
    """
)

JRA_RACES_SQL = text(
    f"""
    INSERT INTO sekito.races AS sr
        (date, course_code, race_no, race_name, race_class, start_time, ground,
         distance, course, weather, condition, total, status, prize,
         info_fetched, result_fetched, grade)
    SELECT
        keiba.to_date_imm(r.date::text, 'YYYYMMDD'),
        sekito.jvlink_to_sekito_course(r.course::text),
        r.race_number,
        r.race_name,
        r.grade,
        (to_timestamp(r.date::text || COALESCE(r.post_time, '0000')::text,
                      'YYYYMMDDHH24MI') AT TIME ZONE 'Asia/Tokyo'),
        r.surface,
        r.distance,
        r.direction,
        r.weather,
        r.condition,
        COALESCE(r.head_count, r.registered_count),
        CASE WHEN COALESCE(r.finishers_count, 0) > 0 THEN 'resulted' ELSE 'init' END,
        CASE WHEN r.prize_1st IS NOT NULL
             THEN concat(r.prize_1st / 100, ',', r.prize_2nd / 100, ',',
                         COALESCE(r.prize_3rd / 100, 0), ',', 0, ',', 0)
             ELSE NULL END,
        true,
        COALESCE(r.finishers_count, 0) > 0,
        r.grade
    FROM keiba.races r
    WHERE r.course = ANY({JRA_COURSES})
      AND r.jravan_race_id IS NOT NULL
      AND keiba.to_date_imm(r.date::text, 'YYYYMMDD') BETWEEN :from_date AND :to_date
    ON CONFLICT (date, course_code, race_no) DO UPDATE
        SET start_time = EXCLUDED.start_time
        WHERE EXCLUDED.start_time::time <> TIME '00:00'
          AND sr.start_time IS DISTINCT FROM EXCLUDED.start_time
    """
)

# chihou.races.date は varchar 'YYYYMMDD'、post_time は 'HHMM'。
# course は netkeiba_id 2桁で sekito.racecourse.netkeiba_id と一致する。
NAR_RACES_SQL = text(
    """
    INSERT INTO sekito.races
        (date, course_code, race_no, race_name, race_class, start_time, ground,
         distance, course, weather, condition, total, status, info_fetched,
         result_fetched, grade)
    SELECT
        to_date(r.date::text, 'YYYYMMDD'),
        rc.code,
        r.race_number,
        r.race_name,
        r.grade,
        to_date(r.date::text, 'YYYYMMDD')
            + (substring(lpad(coalesce(nullif(r.post_time, ''), '0000'), 4, '0')
                         from 1 for 2)
               || ':' ||
               substring(lpad(coalesce(nullif(r.post_time, ''), '0000'), 4, '0')
                         from 3 for 2))::time,
        r.surface,
        r.distance,
        r.direction,
        r.weather,
        r.condition,
        COALESCE(r.head_count, r.registered_count),
        CASE WHEN COALESCE(r.finishers_count, 0) > 0 THEN 'resulted' ELSE 'init' END,
        true,
        COALESCE(r.finishers_count, 0) > 0,
        r.grade
    FROM chihou.races r
    JOIN sekito.racecourse rc ON rc.netkeiba_id = r.course
    WHERE LEFT(rc.code, 1) = 'N'
      AND rc.netkeiba_id <> '65'  -- 帯広ばんえい除外（平地 NAR のみ）
      AND to_date(r.date::text, 'YYYYMMDD') BETWEEN :from_date AND :to_date
    ON CONFLICT (date, course_code, race_no) DO NOTHING
    """
)

COUNT_SQL = text(
    "SELECT count(*) FROM sekito.races WHERE date BETWEEN :from_date AND :to_date"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="keiba / chihou のレースを sekito.kaisai + sekito.races へ供給する"
    )
    parser.add_argument("--from", dest="from_date", help="開始日 YYYY-MM-DD（既定: 今日-7日）")
    parser.add_argument("--to", dest="to_date", help="終了日 YYYY-MM-DD（既定: 2099-12-31）")
    parser.add_argument("--only", choices=("jra", "nar"), help="片方だけ流す")
    parser.add_argument("--dry-run", action="store_true", help="書き込まない")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        stream=sys.stdout)

    today = datetime.now(JST).date()
    from_date = args.from_date or (today - timedelta(days=7)).strftime("%Y-%m-%d")
    to_date = args.to_date or "2099-12-31"
    params = {"from_date": from_date, "to_date": to_date}

    logging.info("=== sekito 供給同期 開始 (%s 〜 %s) ===", from_date, to_date)
    # 🔴 `--dry-run` は記録しない。試し打ちを cron_runs に残すと監視から見て
    #    「ジョブが正常に走った」と区別がつかず、本物の未実行を見逃す。
    ctx = (
        nullcontext(RunRecord(job_name="(記録しない)"))
        if args.dry_run
        else record("sync_sekito_races")
    )
    kaisai = jra = nar = 0
    with ctx as run, SyncSessionLocal() as session:
        before = session.execute(COUNT_SQL, params).scalar_one()

        if args.dry_run:
            logging.info("dry-run: 現在 sekito.races に %d 件（この範囲）", before)
            run.summary = f"dry-run 既存{before}"
            return 0

        if args.only != "nar":
            kaisai = session.execute(KAISAI_SQL, params).rowcount
            jra = session.execute(JRA_RACES_SQL, params).rowcount
            logging.info("JRA: kaisai=%d races=%d", kaisai, jra)
        if args.only != "jra":
            nar = session.execute(NAR_RACES_SQL, params).rowcount
            logging.info("NAR: races=%d", nar)
        session.commit()

        after = session.execute(COUNT_SQL, params).scalar_one()
        latest = session.execute(
            text("SELECT max(date) FROM sekito.races")
        ).scalar_one()
        logging.info("sekito.races: %d → %d 件（最新 %s）", before, after, latest)
        run.summary = (
            f"kaisai{kaisai} JRA{jra} NAR{nar} races{before}→{after} 最新{latest}"
        )

    logging.info("=== sekito 供給同期 終了 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
