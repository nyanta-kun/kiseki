"""特別登録馬の想定騎手を netkeiba から補完する。

JV-Link TOKU データには騎手情報が含まれない（特別登録時点で未確定）が、
netkeiba shutuba.html には想定騎手が表示されているため、それをスクレイプして
keiba.special_registrations.expected_jockey_name を補完する。

使い方:
    .venv/bin/python scripts/scrape_special_jockeys.py
        # expected_jockey_name IS NULL かつ race_date >= today の特別登録馬すべてを対象

    .venv/bin/python scripts/scrape_special_jockeys.py --date 20260509
        # 指定日のみ

    .venv/bin/python scripts/scrape_special_jockeys.py --refresh
        # 既に取得済みも上書き（騎手変更検知用）

レート制限: 1 リクエスト / 2 秒（IP 制限回避）
タイムアウト: 30 秒
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

import requests
from sqlalchemy import create_engine, text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
)
SHUTUBA_URL = "https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
SLEEP_SEC = 2.0
TIMEOUT_SEC = 30


def jravan_to_netkeiba_race_id(jravan_race_id: str) -> str:
    """jravan_race_id (16 chars: YYYY+MMDD+CC+KK+DD+RR) → netkeiba race_id (12 chars: YYYY+CC+KK+DD+RR)。"""
    if len(jravan_race_id) != 16:
        raise ValueError(f"invalid jravan_race_id: {jravan_race_id!r}")
    year = jravan_race_id[0:4]
    course = jravan_race_id[8:10]
    kai = jravan_race_id[10:12]
    day = jravan_race_id[12:14]
    race = jravan_race_id[14:16]
    return f"{year}{course}{kai}{day}{race}"


_HORSE_LIST_RE = re.compile(r'<tr class="HorseList[^"]*"[^>]*>(.*?)</tr>', re.DOTALL)
_CELL_RE = re.compile(r'<td[^>]*class="([^"]+)"[^>]*>(.*?)</td>', re.DOTALL)


def parse_shutuba_html(html: str) -> list[dict]:
    """shutuba.html から馬名・想定騎手を抽出する。

    出馬表確定前は枠番・馬番が空欄になっているが、騎手列には想定騎手が入っている。
    """
    rows = []
    for row_html in _HORSE_LIST_RE.findall(html):
        cells = _CELL_RE.findall(row_html)
        cell_map: dict[str, str] = {}
        for cls, content in cells:
            text_only = re.sub(r"<[^>]+>", "", content).strip()
            # 同じクラスが複数あったら最初のものを使う
            cell_map.setdefault(cls.split()[0], text_only)

        # 馬名: HorseInfo セル内の <a> タグテキスト
        horse_name = cell_map.get("HorseInfo", "").strip()
        # &amp; などをデコード（必要なら）
        horse_name = re.sub(r"&amp;#?\w+;", "", horse_name).replace("&amp;", "")
        jockey = cell_map.get("Jockey", "").strip()

        if horse_name and jockey:
            rows.append({"horse_name": horse_name, "jockey": jockey})
    return rows


def fetch_shutuba(race_id: str) -> str:
    """netkeiba shutuba.html を EUC-JP デコードして返す。"""
    url = SHUTUBA_URL.format(race_id=race_id)
    resp = requests.get(
        url,
        headers={"User-Agent": UA, "Accept-Language": "ja-JP,ja"},
        timeout=TIMEOUT_SEC,
    )
    resp.raise_for_status()
    # netkeiba は EUC-JP
    raw = resp.content
    return raw.decode("euc-jp", errors="replace")


def get_db_engine():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        # .env から組み立て
        env_file = Path(__file__).resolve().parent.parent.parent / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("DB_") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
        host = os.environ["DB_HOST"]
        port = os.environ.get("DB_PORT", "5432")
        name = os.environ["DB_NAME"]
        user = os.environ["DB_USER"]
        password = os.environ["DB_PASSWORD"]
        db_url = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"
    return create_engine(db_url)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="対象日 YYYYMMDD（省略時は今日以降すべて）")
    parser.add_argument(
        "--refresh", action="store_true",
        help="expected_jockey_name が既に入っているレコードも更新する",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="DB UPDATE をスキップしてログ表示のみ（ローカル検証用）",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="処理レース数の上限（テスト用）",
    )
    args = parser.parse_args()

    engine = get_db_engine()

    # 対象レース取得
    if args.date:
        date_clause = "race_date = :race_date"
        params = {"race_date": args.date}
    else:
        date_clause = "race_date >= :today"
        params = {"today": date.today().strftime("%Y%m%d")}

    # dry-run なら expected_jockey_name 列の有無に関係なくシンプルなクエリにする
    if args.dry_run:
        jockey_clause = ""
    elif args.refresh:
        jockey_clause = ""
    else:
        jockey_clause = "AND expected_jockey_name IS NULL"

    with engine.connect() as conn:
        race_rows = conn.execute(text(f"""
            SELECT DISTINCT jravan_race_id, race_date, course_code, race_number
            FROM keiba.special_registrations
            WHERE {date_clause}
            {jockey_clause}
            ORDER BY race_date, course_code, race_number
        """), params).fetchall()

    if args.limit:
        race_rows = race_rows[:args.limit]

    if not race_rows:
        logger.info("対象レースなし")
        return 0

    logger.info(f"対象レース: {len(race_rows)} 件")

    total_horses = 0
    total_updated = 0
    total_failed = 0

    for i, (jravan_race_id, race_date, course_code, race_number) in enumerate(race_rows, 1):
        netkeiba_id = jravan_to_netkeiba_race_id(jravan_race_id)
        logger.info(f"[{i}/{len(race_rows)}] {race_date} {course_code}-{race_number}R "
                    f"jravan={jravan_race_id} netkeiba={netkeiba_id}")
        try:
            html = fetch_shutuba(netkeiba_id)
        except Exception as e:
            logger.error(f"  fetch failed: {e}")
            total_failed += 1
            time.sleep(SLEEP_SEC)
            continue

        horses = parse_shutuba_html(html)
        if not horses:
            logger.warning(f"  no horse rows parsed (HTML may be empty or 404)")
            time.sleep(SLEEP_SEC)
            continue

        # DB の馬を取得
        with engine.connect() as conn:
            db_horses = conn.execute(text("""
                SELECT id, jravan_horse_code, horse_name
                FROM keiba.special_registrations
                WHERE jravan_race_id = :rid
            """), {"rid": jravan_race_id}).fetchall()

        # 馬名一致で UPDATE
        updated_in_race = 0
        if args.dry_run:
            for h in horses:
                target = next(
                    (db for db in db_horses if db.horse_name == h["horse_name"]),
                    None,
                )
                if target:
                    logger.info(f"    [dry-run] {h['horse_name']} → {h['jockey']}")
                    updated_in_race += 1
                else:
                    logger.debug(f"    [dry-run] no DB match: {h['horse_name']}")
        else:
            with engine.begin() as conn:
                for h in horses:
                    target = next(
                        (db for db in db_horses if db.horse_name == h["horse_name"]),
                        None,
                    )
                    if target is None:
                        continue
                    conn.execute(text("""
                        UPDATE keiba.special_registrations
                        SET expected_jockey_name = :jockey,
                            expected_jockey_fetched_at = :now
                        WHERE id = :id
                    """), {
                        "jockey": h["jockey"][:50],
                        "now": datetime.now(),
                        "id": target.id,
                    })
                    updated_in_race += 1

        total_horses += len(horses)
        total_updated += updated_in_race
        logger.info(f"  scraped {len(horses)} horses, matched & updated {updated_in_race} of {len(db_horses)} in DB")

        time.sleep(SLEEP_SEC)

    logger.info(f"完了: {len(race_rows)} レース処理, {total_horses} 騎手スクレイプ, "
                f"{total_updated} 件 UPDATE, {total_failed} 件失敗")
    return 0


if __name__ == "__main__":
    sys.exit(main())
