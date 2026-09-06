"""netkeiba のタイム指数取得（sekito `netkeiba-index` の移設先）。

sekito 側の実体は `bin/scrape/netkeiba` に `--no-paddock` を渡すラッパ
（`bin/scrape/netkeiba-index`、13 行）。中身の `scraping_netkeiba()` は
血統・馬体重・パドックまで抱えた 458 行だったが、それらは

    blood / horse_weight   2026-09-06 に取得対象から外した（JRA-VAN が上位互換）
    paddock                別ジョブ（netkeiba-paddock）

なので、ここはタイム指数だけを見る。

## 🔴 IP 制限ゲートを自分で持つ

sekito では `scheduler.js` がジョブ起動前に弾いていた。kiseki は cron 起動なので
**開始時に加えて、途中でも定期的に確認する**。1 実行が 25 分（2026-09-06 実測）
かかるので、起動時の 1 回だけでは走っている最中に制限を食らったときに
最後まで叩き続けてしまう。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import requests
from sqlalchemy import text
from sqlalchemy.orm import Session

from ...utils.racecourse import BY_CODE
from ..fetch_status import FetchStatusManager
from ..targets import TargetRace
from . import ip_restriction, time_index
from . import race_id as rid
from .decode import decode_page
from .rate_limiter import RateLimiter
from .store import upsert

logger = logging.getLogger(__name__)

DATA_TYPE = "netkeiba_time_index"

# 途中で IP 制限を再確認する間隔（レース数）。25 分かかるジョブなので、
# 起動時の 1 回だけでは足りない。
IP_RECHECK_EVERY = 20


@dataclass
class IndexResult:
    """1 実行ぶんの結果。

    🔴 スキップは異常ではない（既に取得済み・未公開など）。
    「成功 0 件」だけを見て失敗と判定しないこと（PR #493 で同じ誤りを直した）。
    """

    success: int = 0
    skipped: int = 0
    unavailable: int = 0
    errors: int = 0
    aborted_reason: str | None = None
    failed_races: list[str] = field(default_factory=list)

    @property
    def handled(self) -> int:
        return self.success + self.skipped + self.unavailable


_START_TIME_SQL = text(
    """
    SELECT r.post_time, r.jravan_race_id
    FROM keiba.races r
    JOIN keiba.racecourse_map m ON m.jra_code = r.course
    WHERE r.date = :ymd AND m.code = :code AND r.race_number = :no
    """
)

_NAR_START_TIME_SQL = text(
    """
    SELECT r.post_time
    FROM chihou.races r
    JOIN keiba.racecourse_map m ON m.netkeiba_id = r.course AND m.jra_code IS NULL
    WHERE r.date = :ymd AND m.code = :code AND r.race_number = :no
    """
)


def _race_meta(session: Session, target: TargetRace) -> tuple[datetime | None, str | None]:
    """発走時刻と jravan_race_id を引く。

    発走時刻は「タイム指数がまだ出ていないのか、もう出ないのか」の判定に使う
    （`time_index.classify_unavailability`）。取れなくても致命ではない
    （安全側＝未公開として扱われる）。
    """
    ymd = target.date.strftime("%Y%m%d")
    params = {"ymd": ymd, "code": target.course_code, "no": target.race_no}
    sql = _START_TIME_SQL if target.is_jra else _NAR_START_TIME_SQL
    row = session.execute(sql, params).fetchone()
    if row is None:
        return None, None

    post_time = row[0]
    jravan = row[1] if target.is_jra else None

    start: datetime | None = None
    if post_time:
        # post_time は 'hhmm' の 4 文字（keiba.races のコメント参照）
        text_time = str(post_time).strip()
        if len(text_time) == 4 and text_time.isdigit():
            start = datetime.combine(
                target.date,
                datetime.strptime(text_time, "%H%M").time(),
            )
    return start, jravan


def scrape(
    session: Session,
    targets: list[TargetRace],
    *,
    user_id: str,
    password: str,
    environment_id: str = "local",
    dry_run: bool = False,
    force: bool = False,
    limiter: RateLimiter | None = None,
    http: requests.Session | None = None,
) -> IndexResult:
    """対象レースのタイム指数を取得する。

    Args:
        session: 同期 DB セッション。
        targets: `targets.target_races()` の結果。
        user_id / password: netkeiba の認証情報。
        environment_id: IP 制限を記録するときのキー接尾辞。
        dry_run: DB へ書かない。
        force: 取得済みでも取り直す。
        limiter: 差し替え用（省略時は時間帯別の既定）。
        http: 差し替え用のログイン済みセッション（省略時はここでログインする）。

    Returns:
        成功 / スキップ / データ無し / エラーの件数。
    """
    result = IndexResult()
    if not targets:
        logger.info("対象レースがありません")
        return result

    # ① 開始前のゲート。制限中なら 1 リクエストも出さずに戻る。
    try:
        ip_restriction.require_not_restricted(session, context="netkeiba-index")
    except ip_restriction.IPRestricted as e:
        logger.warning("%s", e)
        result.aborted_reason = str(e)
        return result

    limiter = limiter or RateLimiter()
    status = FetchStatusManager(session)

    if http is None:
        from .session import login
        http = login(user_id, password, rate_limiter=limiter)

    for i, target in enumerate(targets):
        # ② 走っている最中に制限を食らうことがある。定期的に見直す。
        if i and i % IP_RECHECK_EVERY == 0:
            try:
                ip_restriction.require_not_restricted(session, context="netkeiba-index")
            except ip_restriction.IPRestricted as e:
                logger.warning("途中で IP 制限を検出したため中断します: %s", e)
                result.aborted_reason = str(e)
                return result

        rc = BY_CODE[target.course_code]
        label = f"{target.group_label} {rc.name} {target.race_no}R"

        if not dry_run and not force and not status.should_fetch(
            target.date, target.course_code, target.race_no, DATA_TYPE
        ):
            result.skipped += 1
            continue

        start_time, jravan = _race_meta(session, target)
        try:
            race_id = (
                rid.jra_race_id(target.date, rc.netkeiba_id, target.race_no, jravan or "")
                if target.is_jra
                else rid.nar_race_id(target.date, rc.netkeiba_id, target.race_no)
            )
        except ValueError as e:
            logger.error("%s: race_id を作れません (%s)", label, e)
            result.errors += 1
            result.failed_races.append(label)
            continue

        url = rid.time_index_url(race_id, is_jra=target.is_jra)
        try:
            limiter.wait()
            response = http.get(url, timeout=30)
            response.raise_for_status()
            page = decode_page(response)
        except Exception as e:
            if ip_restriction.looks_like_ip_restriction(str(e)):
                ip_restriction.mark_restricted(
                    session, source="netkeiba-index", environment_id=environment_id
                )
                logger.error("IP 制限を検出したため中断します: %s", e)
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

        records = time_index.parse(page)
        if not records:
            classification = time_index.classify_unavailability(page, start_time)
            logger.info("%s: タイム指数なし (%s)", label, classification)
            if not dry_run:
                mark = (status.mark_not_yet_published
                        if classification == "not_yet_published"
                        else status.mark_not_available)
                mark(target.date, target.course_code, target.race_no, DATA_TYPE,
                     reason="タイム指数なし")
            result.unavailable += 1
            continue

        if dry_run:
            logger.info("[dry-run] %s: %d 頭 (先頭 馬番%s idx_max=%s)",
                        label, len(records), records[0]["horse_no"], records[0]["idx_max"])
        else:
            for r in records:
                r["is_time_index"] = True
            written = upsert(session, "time_index", target.date,
                             target.course_code, target.race_no, records)
            status.mark_fetched(target.date, target.course_code, target.race_no, DATA_TYPE)
            logger.info("%s: %d 頭", label, written)
        result.success += 1

    logger.info("タイム指数 取得完了 - 成功 %d / スキップ %d / データ無し %d / エラー %d",
                result.success, result.skipped, result.unavailable, result.errors)
    return result
