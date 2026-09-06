"""サラブレ「穴ぐさ」の取得（sekito `bin/scrape/anagusa` からの移設）。

穴ぐさは開催日ごとに 1 ページで全場ぶんが返る。画面上は場タブに見えるが、
初期 HTML に全場の `.race-data` パネルが同梱されていて、タブは CSS による
表示切替でしかない。**1 日 1 リクエストで全場が取れる**（JS 実行不要）。

移設で変えたこと:
    - 場名 → 場コードの解決を `sekito.racecourse` の SELECT から
      `utils/racecourse.py`（Python 側の唯一の出所）へ寄せた。
    - DB アクセスを psycopg2 から SQLAlchemy セッションへ。

移設で変えていないこと（比較検証のため意図的に据え置き）:
    - ログイン手順（CakePHP の CSRF double-submit）
    - パース規則、リトライ回数と待ち時間
    - 書き込み先 `sekito.anagusa` と `sekito.data_fetch_status`
"""

from __future__ import annotations

import logging
import random
import re
import time
from datetime import date as date_type
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..utils.racecourse import BY_NAME
from .fetch_status import FetchStatusManager

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
LOGIN_URL = "https://sarabure.jp/users/login/"
ANAGUSA_LIST_URL = "https://sarabure.jp/anagusa/list/{date_str}"

DATA_TYPE = "anagusa"
MAX_RETRIES = 3


class AnagusaRecord(dict):
    """1 頭ぶんのピック。dict のまま扱う（列は UPSERT の順序と 1:1）。"""


def login(user: str, password: str) -> requests.Session:
    """サラブレにログインして認証 cookie を持つセッションを返す。

    CakePHP の CSRF 防護に対応する:
      1. GET /users/login/ で `_csrfToken`（form 内 hidden）と csrfToken cookie を取る
      2. POST に form 側のトークンを載せる（cookie と double-submit になる）
      3. Origin / Referer と submit ボタン名 `login` も送る

    Raises:
        RuntimeError: トークンが取れない、またはログイン後の HTML に
            ログアウトリンクが現れない（＝ログインできていない）とき。
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ja,en;q=0.5",
    })

    r = session.get(LOGIN_URL, timeout=15)
    r.raise_for_status()
    token_el = BeautifulSoup(r.text, "html.parser").find("input", {"name": "_csrfToken"})
    if not token_el or not token_el.get("value"):
        raise RuntimeError("sarabure: _csrfToken が取れない (HTML 構造が変わった可能性)")

    r2 = session.post(
        LOGIN_URL,
        data={
            "_method": "POST",
            "_csrfToken": token_el["value"],
            "username": user,
            "password": password,
            "login": "",
        },
        headers={"Origin": "https://sarabure.jp", "Referer": LOGIN_URL},
        allow_redirects=True,
        timeout=15,
    )
    r2.raise_for_status()
    if "ログアウト" not in r2.text and "logout" not in r2.text.lower():
        raise RuntimeError(f"sarabure: ログイン失敗 (final url={r2.url}, status={r2.status_code})")
    logger.info("サラブレログイン成功")
    return session


def _date_to_str(value: str | date_type | datetime) -> str:
    if isinstance(value, (date_type, datetime)):
        return value.strftime("%Y%m%d")
    return str(value).replace("-", "")


def fetch_html(session: requests.Session, target_date: str | date_type) -> str:
    """対象日の穴ぐさ一覧 HTML を取得する。"""
    r = session.get(ANAGUSA_LIST_URL.format(date_str=_date_to_str(target_date)), timeout=20)
    r.raise_for_status()
    return r.text


def parse(html: str, target_date: str | date_type) -> list[AnagusaRecord]:
    """穴ぐさ HTML を解析する。

    `.switch`（場ヘッダ）と `.race-data`（場パネル）が文書順で 1:1 に並んでいる前提。
    パネル側の `.place`（"東京 4R"）から場名と R 番号を取り、取れなければ
    場ヘッダの文字列にフォールバックする。
    """
    date_str = _date_to_str(target_date)
    soup = BeautifulSoup(html, "html.parser")
    switches = soup.select(".switch")
    panels = soup.select(".race-data")
    if not switches:
        logger.warning("穴ぐさデータが見つかりません: %s", date_str)
        return []

    records: list[AnagusaRecord] = []
    for sw, panel in zip(switches, panels):
        course_name_default = sw.get_text(strip=True)
        for horse_el in panel.select(".anagusa-horse"):
            try:
                rank_set = set(horse_el.get("class") or []) & {"a", "b", "c"}
                rank = rank_set.pop().upper() if len(rank_set) == 1 else "-"

                place_el = horse_el.select_one(".place")
                place_text = place_el.get_text(strip=True) if place_el else ""
                m = re.match(r"(.+?)\s*([0-9]{1,2})R", place_text)
                if m:
                    course_name, race_no = m.group(1).strip(), int(m.group(2))
                else:
                    course_name, race_no = course_name_default, 0

                horse_no, horse_name = 0, "-"
                name_span = horse_el.select_one(".name span")
                if name_span:
                    m2 = re.match(r"([0-9]{1,2})\s*(.+)", name_span.get_text(strip=True))
                    if m2:
                        horse_no, horse_name = int(m2.group(1)), m2.group(2).strip()

                box_el = horse_el.select_one(".anagusa-box")
                records.append(AnagusaRecord(
                    date=date_str,
                    course_name=course_name,
                    race_no=race_no,
                    horse_no=horse_no,
                    horse_name=horse_name,
                    rank=rank,
                    comment=box_el.get_text(strip=True) if box_el else "",
                ))
            except Exception as e:  # 1 頭の崩れで全体を落とさない
                logger.warning("馬データ解析エラー: %s", e)
                continue
    return records


_UPSERT = text(
    """
    INSERT INTO sekito.anagusa (date, course_code, race_no, horse_no, horse_name, rank, comment)
    VALUES (:date, :course_code, :race_no, :horse_no, :horse_name, :rank, :comment)
    ON CONFLICT (date, course_code, race_no, horse_no)
    DO UPDATE SET horse_name = EXCLUDED.horse_name,
                  rank = EXCLUDED.rank,
                  comment = EXCLUDED.comment
    """
)


def upsert(session: Session, records: list[AnagusaRecord]) -> int:
    """`sekito.anagusa` へ UPSERT し、書けた件数を返す。

    場名が対応表に無いレコードは落とす（移設前も `sekito.racecourse` に
    無ければ落としていた）。落とした場名は WARN に出す — 場名の表記が
    変わったときに黙って 0 件にならないようにするため。
    """
    if not records:
        logger.info("登録対象データなし")
        return 0

    rows, unresolved = [], set()
    for r in records:
        rc = BY_NAME.get(r["course_name"])
        if rc is None:
            unresolved.add(r["course_name"])
            continue
        rows.append({
            "date": r["date"], "course_code": rc.code, "race_no": int(r["race_no"]),
            "horse_no": int(r["horse_no"]), "horse_name": r["horse_name"],
            "rank": r["rank"], "comment": r["comment"],
        })

    if unresolved:
        logger.warning("場名を解決できませんでした（対応表に無い）: %s", sorted(unresolved))
    if not rows:
        logger.warning("course_code 解決できず登録 0 件 (元 records=%d)", len(records))
        return 0

    session.execute(_UPSERT, rows)
    session.commit()
    logger.info("anagusa テーブル UPSERT: %d 件", len(rows))
    return len(rows)


def mark_fetched(session: Session, records: list[AnagusaRecord]) -> None:
    """ピックが付いたレースを取得済みとして記録する。

    ⚠️ 穴ぐさは**全レースにピックが付くわけではない**。ここで記録できるのは
    「ピックがあったレース」だけで、ピック 0 のレースは未着手のまま残る。
    移設前もそうなっていて、穴ぐさの網羅率を比率で見てはいけない理由でもある。
    """
    status = FetchStatusManager(session)
    seen: set[tuple] = set()
    for r in records:
        rc = BY_NAME.get(r["course_name"])
        if rc is None:
            continue
        key = (r["date"], rc.code, r["race_no"])
        if key in seen:
            continue
        seen.add(key)
        status.mark_fetched(r["date"], rc.code, r["race_no"], DATA_TYPE, commit=False)
    session.commit()


def scrape(
    session: Session,
    target_date: date_type,
    user: str,
    password: str,
    *,
    dry_run: bool = False,
) -> list[AnagusaRecord]:
    """穴ぐさを 1 日ぶん取得して DB へ入れる。

    ログイン〜取得は最大 3 回まで再試行する（指数バックオフ + ジッタ）。
    """
    logger.info("穴ぐさ取得: date=%s dry_run=%s", target_date, dry_run)

    for attempt in range(MAX_RETRIES):
        try:
            session_http = login(user, password)
            records = parse(fetch_html(session_http, target_date), target_date)
            logger.info("穴ぐさデータ取得完了: %d 件", len(records))

            if dry_run:
                logger.info("[dry-run] DB 書き込みスキップ")
                return records

            upsert(session, records)
            mark_fetched(session, records)
            return records
        except Exception as e:
            logger.error("穴ぐさ取得試行 %d/%d でエラー: %s", attempt + 1, MAX_RETRIES, e)
            if attempt == MAX_RETRIES - 1:
                logger.error("穴ぐさ取得に失敗しました（最大リトライ回数に達しました）")
                raise
            wait = 2 ** attempt + random.uniform(1, 3)
            logger.info("%.1f 秒待機後にリトライします", wait)
            time.sleep(wait)
    return []
