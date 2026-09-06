"""パドックの発走前ウォッチャー（sekito `netkeiba-paddock` の移設先）。

3 分ごとに起動し、**発走 20 分以内 かつ 未取得** の中央レースだけを取りに行く。
対象が無ければ 1 リクエストも出さずに終わる。

## 🔴 発走時刻の取り方を変えた

移設元は `sekito.races.start_time` を見ていた。その表は sekito の日次同期が
`keiba.races` から作っており、**`ON CONFLICT DO NOTHING` のせいで start_time が
00:00 に固定されていた**。結果「発走 20 分以内」に永久に一致せず、3 分ごとに
「対象なし」と success を返し続けて、パドック指数が 7 月以降ずっと 0 件だった
（2026-09-05 の障害調査で判明）。

kiseki は `keiba.races.post_time`（'hhmm' の 4 文字）を直接読む。同期を挟まないので
この壊れ方が構造的に起きない。

## 20 分という窓

パドックの評価は発走の 20〜10 分前に出る。窓を狭めると取りこぼし、広げると
未公開のページを何度も叩くことになる。移設元の 20 分をそのまま使う。

取得済み・データ無し・中止のレースは `data_fetch_status` で除外するので、
同じレースを何度も取りに行くことはない。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import text
from sqlalchemy.orm import Session

from ...utils.racecourse import BY_CODE
from ..fetch_status import FetchStatusManager, detect_race_cancellation
from ..targets import TargetRace
from . import ip_restriction, paddock
from . import race_id as rid
from .decode import decode_page
from .rate_limiter import RateLimiter
from .store import upsert

logger = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")

DATA_TYPE = "netkeiba_paddock"

# パドック評価が出てから発走までの窓（分）。
WATCH_WINDOW_MINUTES = 20


@dataclass
class PaddockResult:
    """1 実行ぶんの結果。対象 0 件は正常（ほとんどの実行がそれ）。"""

    targets: int = 0
    success: int = 0
    unavailable: int = 0
    errors: int = 0
    aborted_reason: str | None = None
    failed_races: list[str] = field(default_factory=list)


# 発走 20 分以内・未取得の中央レース。
# `post_time` は 'hhmm' の 4 文字なので、日付と繋いで時刻に直して比較する。
_PENDING_SQL = text(
    """
    SELECT m.code AS course_code, r.race_number, r.post_time, r.jravan_race_id
    FROM keiba.races r
    JOIN keiba.racecourse_map m ON m.jra_code = r.course
    WHERE r.date = :ymd
      AND r.post_time IS NOT NULL
      AND r.post_time <> ''
      AND (to_date(r.date, 'YYYYMMDD')
           + (substr(r.post_time, 1, 2) || ':' || substr(r.post_time, 3, 2))::time)
          BETWEEN :now AND :until
      AND NOT EXISTS (
          SELECT 1 FROM sekito.data_fetch_status dfs
          WHERE dfs.date = to_date(r.date, 'YYYYMMDD')
            AND dfs.course_code = m.code
            AND dfs.race_no = r.race_number
            AND dfs.data_type = :data_type
            AND dfs.fetch_status IN ('fetched', 'not_available', 'race_cancelled')
      )
    ORDER BY r.post_time, m.code, r.race_number
    """
)


def pending_races(session: Session, *, now: datetime | None = None
                  ) -> list[tuple[TargetRace, str]]:
    """いま取りに行くべきレースを返す。

    Returns:
        `(TargetRace, jravan_race_id)` の並び。発走時刻の昇順。
    """
    now = now or datetime.now(JST)
    today = now.date()
    rows = session.execute(_PENDING_SQL, {
        "ymd": today.strftime("%Y%m%d"),
        "now": now.replace(tzinfo=None),
        "until": (now + timedelta(minutes=WATCH_WINDOW_MINUTES)).replace(tzinfo=None),
        "data_type": DATA_TYPE,
    }).all()

    out: list[tuple[TargetRace, str]] = []
    for course_code, race_no, _post_time, jravan in rows:
        out.append((TargetRace(today, course_code, int(race_no)), jravan or ""))

    logger.info("発走%d分以内・パドック未取得: %d 件 (%s 〜 %s)",
                WATCH_WINDOW_MINUTES, len(out), now.strftime("%H:%M"),
                (now + timedelta(minutes=WATCH_WINDOW_MINUTES)).strftime("%H:%M"))
    return out


def scrape(
    session: Session,
    races: list[tuple[TargetRace, str]],
    *,
    user_id: str,
    password: str,
    environment_id: str = "local",
    dry_run: bool = False,
    limiter: RateLimiter | None = None,
    http: requests.Session | None = None,
) -> PaddockResult:
    """対象レースのパドック評価を取得する。"""
    result = PaddockResult(targets=len(races))
    if not races:
        return result

    try:
        ip_restriction.require_not_restricted(session, context="netkeiba-paddock")
    except ip_restriction.IPRestricted as e:
        logger.warning("%s", e)
        result.aborted_reason = str(e)
        return result

    limiter = limiter or RateLimiter()
    status = FetchStatusManager(session)

    if http is None:
        from .session import login
        http = login(user_id, password, rate_limiter=limiter)

    for target, jravan in races:
        rc = BY_CODE[target.course_code]
        label = f"{rc.name} {target.race_no}R"

        try:
            race_id = rid.jra_race_id(target.date, rc.netkeiba_id, target.race_no, jravan)
        except ValueError as e:
            logger.error("%s: race_id を作れません (%s)", label, e)
            result.errors += 1
            result.failed_races.append(label)
            continue

        try:
            limiter.wait()
            response = http.get(rid.paddock_url(race_id), timeout=30)
            response.raise_for_status()
            page = decode_page(response)
        except Exception as e:
            if ip_restriction.looks_like_ip_restriction(str(e)):
                ip_restriction.mark_restricted(
                    session, source="netkeiba-paddock", environment_id=environment_id
                )
                result.aborted_reason = str(e)
                return result
            logger.error("%s の取得でエラー: %s", label, e)
            if not dry_run:
                session.rollback()
                status.mark_failed(target.date, target.course_code,
                                   target.race_no, DATA_TYPE, str(e))
            result.errors += 1
            result.failed_races.append(label)
            continue

        if detect_race_cancellation(page):
            logger.warning("レース中止検出: %s", label)
            if not dry_run:
                status.mark_race_cancelled(target.date, target.course_code, target.race_no)
            continue

        records = paddock.parse(page)
        if not records:
            # 発走前で評価がまだ出ていないことがある。恒久的な不在にはしない
            # （`not_available` にすると 6 時間取りに行かなくなり、当日を取り逃す）。
            logger.info("%s: パドック評価はまだ出ていません", label)
            if not dry_run:
                status.mark_not_yet_published(
                    target.date, target.course_code, target.race_no, DATA_TYPE,
                    reason="パドック評価が未公開",
                )
            result.unavailable += 1
            continue

        if dry_run:
            logger.info("[dry-run] %s: %d 頭 (先頭 %s %s)",
                        label, len(records), records[0]["p_type"], records[0]["p_rank"])
        else:
            for r in records:
                r["is_paddock"] = True
            written = upsert(session, "paddock", target.date,
                             target.course_code, target.race_no, records)
            status.mark_fetched(target.date, target.course_code, target.race_no, DATA_TYPE)
            logger.info("%s: %d 頭", label, written)
        result.success += 1

    return result
