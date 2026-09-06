"""スクレイプ取得状態の記録（`sekito.data_fetch_status`）。

sekito の `lib/sekito/utils/fetch_status_manager.py` からの移設。psycopg2 直結を
SQLAlchemy セッションへ置き換えただけで、**状態遷移と再試行間隔は 1:1 で保っている**。
ここを変えると移設前後で「取りに行く/行かない」がずれ、比較検証が成立しなくなる。

状態と再試行の設計（移設前の実装のまま）:

    fetched            取得済み。二度と取りに行かない。
    race_cancelled     レース中止。二度と取りに行かない。
    not_available      データが無い。`netkeiba_time_index` だけ 1 時間後、
                       それ以外は 6 時間後に再試行する。
    not_yet_published  まだ公開されていない（レース前の time_index 等）。1 時間後に再試行。
    fetch_failed       取得に失敗。1 時間空けて最大 `max_retries`(既定3) 回まで再試行。
    （レコード無し）    未着手。取りに行く。

⚠️ 書き込み先は移設時点では sekito スキーマのまま。sekito 側のスクレイパと
   同じ表を共有することで、**1 本ずつ切り替えても取得済み情報が失われない**。
   先に keiba スキーマへ移すと、切り替えた瞬間に全レースを取り直しにいく。
"""

from __future__ import annotations

import logging
from datetime import date as date_type
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# `mark_race_cancelled` が一括で潰すデータタイプ。移設前の一覧をそのまま持つ。
ALL_DATA_TYPES: tuple[str, ...] = (
    "entries",
    "odds",
    "results",
    "kichiuma",
    "anagusa",
    "netkeiba_time_index",
    "netkeiba_blood",
    "netkeiba_paddock",
    "netkeiba_training",
    "netkeiba_data_analysis",
    "horse_weight",
)

# `not_available` からの再試行間隔（時間）。time_index だけ短いのは、
# レース直前に公開されることがあるため。
_NOT_AVAILABLE_RETRY_HOURS = {"netkeiba_time_index": 1}
_NOT_AVAILABLE_RETRY_HOURS_DEFAULT = 6

# 🔴 経過時間は **DB 側で** 計算する。Python の `datetime.now()` と比べてはいけない。
#
#   2026-09-06 実測: DB は Asia/Tokyo（`updated_at` は timestamp without time zone に
#   JST の値が入る）。移設元の sekito コンテナは JST なので naive 比較で合っていたが、
#   移設先の kiseki backend コンテナは **UTC**。そのまま持ってくると経過時間が
#   9 時間ぶん過小に出て、6 時間の再試行が実質 15 時間後、1 時間の再試行が
#   実質 10 時間後になる。**取りに行かなすぎる方向に、例外を出さずに壊れる。**
#   NOW() 同士で引けばコンテナの TZ に依存しない。
_SHOULD_FETCH_SQL = text(
    """
    SELECT fetch_status,
           retry_count,
           EXTRACT(EPOCH FROM (NOW() - updated_at)) / 3600.0 AS elapsed_hours
    FROM sekito.data_fetch_status
    WHERE date = :d AND course_code = :c AND race_no = :n AND data_type = :t
    """
)


def _norm_date(value: str | date_type | datetime) -> str:
    """日付を 'YYYY-MM-DD' に正規化する（'YYYYMMDD' も受ける）。"""
    s = str(value)
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


class FetchStatusManager:
    """取得状態の読み書き。

    セッションは呼び出し側が持つ。移設前は「1 レース 1 メソッドごとに psycopg2 接続を
    張り直す」形で、1 レースあたり最大 4 接続を開いていたが、ここでは
    スクレイパ 1 実行につき 1 セッションを使い回す。
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def should_fetch(
        self,
        date: str | date_type,
        course_code: str,
        race_no: int,
        data_type: str,
        *,
        max_retries: int = 3,
        force: bool = False,
    ) -> bool:
        """取得しに行くべきか。"""
        if force:
            return True

        row = self.session.execute(_SHOULD_FETCH_SQL, {
            "d": _norm_date(date), "c": course_code, "n": race_no, "t": data_type,
        }).fetchone()

        if row is None:
            return True

        status, retry_count, elapsed_hours = row
        # updated_at が NULL のときは SQL 側も NULL を返す。移設前は
        # `updated_at and ...` で「経過済み」として扱っていたのでそれに合わせる。
        elapsed = float("inf") if elapsed_hours is None else float(elapsed_hours)

        if status in ("fetched", "race_cancelled"):
            return False

        if status == "not_available":
            hours = _NOT_AVAILABLE_RETRY_HOURS.get(data_type, _NOT_AVAILABLE_RETRY_HOURS_DEFAULT)
            return elapsed >= hours

        if status == "not_yet_published":
            return elapsed >= 1

        if status == "fetch_failed":
            if (retry_count or 0) >= max_retries:
                logger.warning(
                    "最大リトライ回数超過: %s %s %sR %s (retry_count=%s)",
                    _norm_date(date), course_code, race_no, data_type, retry_count,
                )
                return False
            return elapsed >= 1

        # pending / 未知の状態は取りに行く
        return True

    def get_status(
        self, date: str | date_type, course_code: str, race_no: int, data_type: str
    ) -> tuple[str, str | None] | None:
        """(fetch_status, error_message) を返す。レコードが無ければ None。"""
        row = self.session.execute(
            text(
                """
                SELECT fetch_status, error_message
                FROM sekito.data_fetch_status
                WHERE date = :d AND course_code = :c AND race_no = :n AND data_type = :t
                """
            ),
            {"d": _norm_date(date), "c": course_code, "n": race_no, "t": data_type},
        ).fetchone()
        return (row[0], row[1]) if row else None

    def mark_fetched(
        self, date: str | date_type, course_code: str, race_no: int, data_type: str,
        *, commit: bool = True,
    ) -> None:
        """取得成功を記録する（error_message と retry_count をクリアする）。"""
        self.session.execute(
            text(
                """
                INSERT INTO sekito.data_fetch_status
                    (date, course_code, race_no, data_type, fetch_status, fetched_at, updated_at)
                VALUES (:d, :c, :n, :t, 'fetched', NOW(), NOW())
                ON CONFLICT (date, course_code, race_no, data_type)
                DO UPDATE SET fetch_status = 'fetched', fetched_at = NOW(),
                              error_message = NULL, retry_count = 0, updated_at = NOW()
                """
            ),
            {"d": _norm_date(date), "c": course_code, "n": race_no, "t": data_type},
        )
        if commit:
            self.session.commit()

    def mark_not_available(
        self, date: str | date_type, course_code: str, race_no: int, data_type: str,
        *, reason: str | None = None, commit: bool = True,
    ) -> None:
        """データ不在（恒久的とみなす）を記録する。既定 6 時間後に再試行される。"""
        self._mark_status(date, course_code, race_no, data_type, "not_available", reason, commit)

    def mark_not_yet_published(
        self, date: str | date_type, course_code: str, race_no: int, data_type: str,
        *, reason: str | None = None, commit: bool = True,
    ) -> None:
        """未公開（一時的）を記録する。1 時間後に再試行される。"""
        self._mark_status(date, course_code, race_no, data_type, "not_yet_published", reason, commit)

    def _mark_status(
        self, date, course_code: str, race_no: int, data_type: str,
        status: str, reason: str | None, commit: bool,
    ) -> None:
        self.session.execute(
            text(
                """
                INSERT INTO sekito.data_fetch_status
                    (date, course_code, race_no, data_type, fetch_status, error_message, updated_at)
                VALUES (:d, :c, :n, :t, :s, :msg, NOW())
                ON CONFLICT (date, course_code, race_no, data_type)
                DO UPDATE SET fetch_status = EXCLUDED.fetch_status,
                              error_message = EXCLUDED.error_message, updated_at = NOW()
                """
            ),
            {"d": _norm_date(date), "c": course_code, "n": race_no, "t": data_type,
             "s": status, "msg": reason},
        )
        if commit:
            self.session.commit()

    def mark_failed(
        self, date: str | date_type, course_code: str, race_no: int, data_type: str,
        error_msg: str, *, commit: bool = True,
    ) -> None:
        """取得失敗を記録し、retry_count を 1 増やす。"""
        self.session.execute(
            text(
                """
                INSERT INTO sekito.data_fetch_status
                    (date, course_code, race_no, data_type, fetch_status,
                     error_message, retry_count, updated_at)
                VALUES (:d, :c, :n, :t, 'fetch_failed', :msg, 1, NOW())
                ON CONFLICT (date, course_code, race_no, data_type)
                DO UPDATE SET fetch_status = 'fetch_failed',
                              error_message = EXCLUDED.error_message,
                              retry_count = COALESCE(data_fetch_status.retry_count, 0) + 1,
                              updated_at = NOW()
                """
            ),
            {"d": _norm_date(date), "c": course_code, "n": race_no, "t": data_type,
             "msg": error_msg},
        )
        if commit:
            self.session.commit()
        logger.error("取得失敗記録: %s %s %sR %s - %s",
                     _norm_date(date), course_code, race_no, data_type, error_msg)

    def mark_race_cancelled(
        self, date: str | date_type, course_code: str, race_no: int,
        *, commit: bool = True, update_sekito_races: bool = True,
    ) -> None:
        """レース中止を記録し、全データタイプを race_cancelled にする。

        Args:
            update_sekito_races: `sekito.races.status` も 'cancelled' にするか。
                sekito の画面が同じ表を読んでいるあいだは True のままにしておく。
                **sekito 廃止時に消す引数**。
        """
        d = _norm_date(date)
        if update_sekito_races:
            self.session.execute(
                text(
                    "UPDATE sekito.races SET status = 'cancelled' "
                    "WHERE date = :d AND course_code = :c AND race_no = :n"
                ),
                {"d": d, "c": course_code, "n": race_no},
            )
        for data_type in ALL_DATA_TYPES:
            self.session.execute(
                text(
                    """
                    INSERT INTO sekito.data_fetch_status
                        (date, course_code, race_no, data_type, fetch_status,
                         error_message, updated_at)
                    VALUES (:d, :c, :n, :t, 'race_cancelled', 'レース中止', NOW())
                    ON CONFLICT (date, course_code, race_no, data_type)
                    DO UPDATE SET fetch_status = 'race_cancelled',
                                  error_message = 'レース中止', updated_at = NOW()
                    """
                ),
                {"d": d, "c": course_code, "n": race_no, "t": data_type},
            )
        if commit:
            self.session.commit()
        logger.warning("レース中止記録: %s %s %sR", d, course_code, race_no)


# レース中止を示す文言。移設前の一覧をそのまま持つ。
_CANCEL_KEYWORDS = (
    "レース中止", "レース取消", "中止になりました",
    "取消になりました", "取り消しになりました",
    "race cancelled", "開催中止", "このレースは中止",
)


def detect_race_cancellation(html: str) -> bool:
    """取得した HTML がレース中止を示しているか。"""
    for keyword in _CANCEL_KEYWORDS:
        if keyword in html:
            logger.info("レース中止検出: キーワード='%s'", keyword)
            return True
    return False
