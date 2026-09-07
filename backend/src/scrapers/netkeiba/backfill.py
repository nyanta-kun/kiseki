"""化けた列を netkeiba から取り直して埋め戻す。

## なぜ再取得しかないのか

`errors="replace"` は復号できないバイト列を **U+FFFD 1 文字に潰す**。潰した時点で
元のバイトは失われるので、**文字列だけを見て元に戻すことは原理的にできない**。

馬名と血統（中央）は netkeiba 固有の情報ではないので JV-Link / UmaConn から
書き戻せた（`scripts/repair_netkeiba_horse_names.py`・2026-09-06 に適用済み）。
残るのは netkeiba にしか無いものだけ:

    血統(地方)  1,550 行 / 402 レース   中央登録歴の無い地方専用馬の父・母父
    調教        4,754 行 / 366 レース   "キビキビ A" 等の netkeiba 独自の短評
    寸評          391 行 /  73 レース   パドックの寸評

計 841 リクエスト。夜間（レート制限が最も緩い時間帯）で 1.5〜2 時間の見込み。

## 時間で打ち切る

既存の `backfill-netkeiba-time-index`（sekito id=96・110 分予算）と同じ考え方で、
**予算時間を過ぎたら途中でも止める**。1 晩で終わらなければ次の晩に続きをやる
（対象は「まだ化けている行」で決まるので、状態を持たずに再開できる）。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date

import requests
from sqlalchemy import text
from sqlalchemy.orm import Session

from ...utils.racecourse import BY_CODE
from ..targets import TargetRace
from . import blood, ip_restriction, paddock, time_index, training
from . import race_id as rid
from .decode import decode_page
from .rate_limiter import RateLimiter
from .store import REPLACEMENT_CHAR, upsert

logger = logging.getLogger(__name__)

# 化けを直す対象 → 対象行を選ぶ条件。
# ⚠️ `time_index` はここに入らない。**化けではなく「行が無い」**ことを直すので、
#    行に対する条件では表現できない（`missing_time_index_races()` を使う）。
TARGETS: dict[str, str] = {
    "blood": "position(:bad in coalesce(sire,'')) > 0 "
             "OR position(:bad in coalesce(broodmare_sire,'')) > 0",
    "training": "position(:bad in coalesce(training,'')) > 0",
    "paddock": "position(:bad in coalesce(p_comment,'')) > 0 "
               "OR position(:bad in coalesce(p_rank,'')) > 0",
}


# 取得対象 → 「取得済み」を示す列。
_IS_FLAG = {"blood": "is_blood", "training": "is_training",
            "paddock": "is_paddock", "time_index": "is_time_index"}

# タイム指数の埋め戻しを始める日。requests 移行（2026-05-04）以降が欠けている。
TIME_INDEX_FROM = date(2026, 5, 4)


@dataclass
class BackfillResult:
    """1 実行ぶんの結果。"""

    targets: int = 0
    success: int = 0
    empty: int = 0
    errors: int = 0
    aborted_reason: str | None = None
    failed_races: list[str] = field(default_factory=list)


def pending_races(session: Session, target: str, *, limit: int | None = None
                  ) -> list[tuple[TargetRace, str]]:
    """まだ化けているレースを、古い順に返す。

    Returns:
        `(TargetRace, jravan_race_id)` の並び。地方は jravan_race_id が空になる。
    """
    condition = TARGETS.get(target)
    if condition is None:
        raise ValueError(f"未知の取得対象: {target}")

    rows = session.execute(
        text(
            f"""
            SELECT DISTINCT n.date, n.course_code, n.race_no,
                   coalesce(kr.jravan_race_id, '') AS jravan
            FROM sekito.netkeiba n
            JOIN keiba.racecourse_map m ON m.code = n.course_code
            LEFT JOIN keiba.races kr
                   ON m.jra_code IS NOT NULL
                  AND kr.date = to_char(n.date, 'YYYYMMDD')
                  AND kr.course = m.jra_code
                  AND kr.race_number = n.race_no
            WHERE {condition}
            ORDER BY n.date, n.course_code, n.race_no
            {"LIMIT :limit" if limit else ""}
            """
        ),
        {"bad": REPLACEMENT_CHAR, **({"limit": limit} if limit else {})},
    ).all()

    return [(TargetRace(d, cc, int(rn)), jravan) for d, cc, rn, jravan in rows]


_MISSING_TIME_INDEX_SQL = text(
    """
    WITH supply AS (
      SELECT to_date(r.date, 'YYYYMMDD') AS d, m.code AS course_code,
             r.race_number AS race_no, coalesce(r.jravan_race_id, '') AS jravan
      FROM keiba.races r
      JOIN keiba.racecourse_map m ON m.jra_code = r.course
      WHERE r.date BETWEEN :ymd_from AND :ymd_to
        -- 🔴 新馬戦にタイム指数は**原理的に存在しない**（過去走から作る指標のため）。
        --    2026-09-07 実測: 該当 111 件すべてで指数なし。叩くだけ無駄になる。
        AND coalesce(r.race_condition_code, '') <> '701'
      UNION ALL
      SELECT to_date(r.date, 'YYYYMMDD'), m.code, r.race_number, ''
      FROM chihou.races r
      JOIN keiba.racecourse_map m ON m.netkeiba_id = r.course AND m.jra_code IS NULL
      WHERE r.date BETWEEN :ymd_from AND :ymd_to
      -- 地方には競走条件コードが無いので新馬の除外ができない。
      -- 取りに行って空なら not_available を記録するので、無駄は 1 回で止まる。
    )
    SELECT s.d, s.course_code, s.race_no, s.jravan
    FROM supply s
    WHERE NOT EXISTS (
        SELECT 1 FROM sekito.netkeiba n
        WHERE n.date = s.d AND n.course_code = s.course_code
          AND n.race_no = s.race_no AND n.idx_max IS NOT NULL)
      AND NOT EXISTS (
        -- 一度取りに行って「無い」と分かったレースは二度と叩かない。
        -- ⚠️ 移設元は毎晩の対象を「指数が無いレース」だけで決めていたので、
        --    埋まらないレースを**毎晩ずっと叩き続ける**形になっていた。
        SELECT 1 FROM sekito.data_fetch_status f
        WHERE f.date = s.d AND f.course_code = s.course_code
          AND f.race_no = s.race_no AND f.data_type = 'netkeiba_time_index'
          AND f.fetch_status IN ('not_available', 'race_cancelled'))
    ORDER BY s.d, s.course_code, s.race_no
    """
)


def missing_time_index_races(
    session: Session, *, date_from: date | None = None, date_to: date | None = None,
    limit: int | None = None,
) -> list[tuple[TargetRace, str]]:
    """タイム指数が入っていないレースを古い順に返す。

    化けの修復とは別で、**そもそも行が無い**ものを埋める。
    2026-05 以降ずっと `netkeiba-index` が 10 分でタイムアウト kill されていたため、
    約 3,600 レースぶんが欠けている（2026-09-07 実測 3,607・115 日ぶん）。
    """
    rows = session.execute(_MISSING_TIME_INDEX_SQL, {
        "ymd_from": (date_from or TIME_INDEX_FROM).strftime("%Y%m%d"),
        "ymd_to": (date_to or date.today()).strftime("%Y%m%d"),
    }).all()
    out = [(TargetRace(d, cc, int(rn)), j) for d, cc, rn, j in rows]
    return out[:limit] if limit else out


def _url_for(target: str, race: TargetRace, race_id: str) -> str | None:
    if target == "time_index":
        return rid.time_index_url(race_id, is_jra=race.is_jra)
    if target == "blood":
        return rid.blood_url(race_id, is_jra=race.is_jra)
    if target == "training":
        # 調教は中央のみ。地方に oikiri.html は無い。
        return rid.training_url(race_id) if race.is_jra else None
    if target == "paddock":
        return rid.paddock_url(race_id) if race.is_jra else None
    return None


def _parse_for(target: str, page: str) -> list[dict]:
    if target == "time_index":
        return time_index.parse(page)
    if target == "blood":
        return blood.parse(page)
    if target == "training":
        return training.parse(page)
    return paddock.parse(page)


def run(
    session: Session,
    target: str,
    races: list[tuple[TargetRace, str]],
    *,
    user_id: str,
    password: str,
    environment_id: str = "local",
    budget_seconds: float | None = None,
    dry_run: bool = False,
    limiter: RateLimiter | None = None,
    http: requests.Session | None = None,
) -> BackfillResult:
    """対象レースを取り直して埋め戻す。

    Args:
        budget_seconds: これを超えたら途中で止める。None なら最後まで。
    """
    result = BackfillResult(targets=len(races))
    if not races:
        return result

    try:
        ip_restriction.require_not_restricted(session, context=f"backfill:{target}")
    except ip_restriction.IPRestricted as e:
        logger.warning("%s", e)
        result.aborted_reason = str(e)
        return result

    limiter = limiter or RateLimiter()
    if http is None:
        from .session import login
        http = login(user_id, password, rate_limiter=limiter)

    started = time.monotonic()
    for i, (race, jravan) in enumerate(races):
        if budget_seconds is not None and time.monotonic() - started > budget_seconds:
            logger.info("予算時間(%.0f秒)に達したので中断します（%d/%d 件処理済み）",
                        budget_seconds, i, len(races))
            result.aborted_reason = "budget"
            break

        # 長い実行なので途中でも IP 制限を見直す。
        if i and i % 20 == 0:
            try:
                ip_restriction.require_not_restricted(session, context=f"backfill:{target}")
            except ip_restriction.IPRestricted as e:
                logger.warning("途中で IP 制限を検出したため中断します: %s", e)
                result.aborted_reason = str(e)
                break

        rc = BY_CODE[race.course_code]
        label = f"{race.group_label} {rc.name} {race.date} {race.race_no}R"

        try:
            race_id = (
                rid.jra_race_id(race.date, rc.netkeiba_id, race.race_no, jravan)
                if race.is_jra
                else rid.nar_race_id(race.date, rc.netkeiba_id, race.race_no)
            )
        except ValueError as e:
            logger.error("%s: race_id を作れません (%s)", label, e)
            result.errors += 1
            result.failed_races.append(label)
            continue

        url = _url_for(target, race, race_id)
        if url is None:
            continue

        try:
            limiter.wait()
            response = http.get(url, timeout=30)
            response.raise_for_status()
            records = _parse_for(target, decode_page(response))
        except Exception as e:
            if ip_restriction.looks_like_ip_restriction(str(e)):
                ip_restriction.mark_restricted(
                    session, source=f"backfill:{target}", environment_id=environment_id
                )
                result.aborted_reason = str(e)
                break
            logger.error("%s の取得でエラー: %s", label, e)
            result.errors += 1
            result.failed_races.append(label)
            continue

        if not records:
            # 古いレースはページが残っていないことがある。化けたまま残るが、
            # 上書きできる材料が無いので触らない。
            logger.info("%s: データが取れませんでした（ページが残っていない可能性）", label)
            result.empty += 1
            continue

        if dry_run:
            logger.info("[dry-run] %s: %d 頭", label, len(records))
        else:
            # 取得できたことを示すフラグ。日次ジョブと同じ列を立てる
            # （立て忘れると NOT NULL の `is_*` に NULL を書こうとして落ちる）。
            for r in records:
                r[_IS_FLAG[target]] = True
            upsert(session, target, race.date, race.course_code, race.race_no, records)
            logger.info("%s: %d 頭", label, len(records))
        result.success += 1

    logger.info("埋め戻し(%s) 完了 - 成功 %d / 空 %d / エラー %d（対象 %d）",
                target, result.success, result.empty, result.errors, result.targets)
    return result
