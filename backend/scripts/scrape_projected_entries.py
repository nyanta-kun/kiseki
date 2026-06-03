"""netkeiba から「全レースの出走想定」を取得する（新馬・未勝利・条件戦含む）。

JV-Link の TOKU（特別登録）は特別競走のみが対象で、新馬戦などの想定は含まない。
netkeiba の出走想定は非特別レースについてはプレミアム会員限定だが、ログイン
セッションを使えば全レースの想定馬・想定騎手を取得できる（確定出馬表の数日前から）。

認証: .env の NETKEIBA_USER_ID / NETKEIBA_PASSWORD でプログラムログイン。
レース列挙: race_list_sub.html?kaisai_date=YYYYMMDD から race_id を抽出。
想定取得: shutuba.html?race_id=... をログインセッションで取得しパース。

使い方:
    .venv/bin/python scripts/scrape_projected_entries.py --dates 20260606,20260607
    .venv/bin/python scripts/scrape_projected_entries.py --dates 20260607 --limit 3 --out /tmp/p.json

レート制限: 1 リクエスト / 2 秒（IP 制限回避）。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import psycopg2
import requests
from dotenv import load_dotenv
from psycopg2.extras import execute_values

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15"
)
LOGIN_PAGE = "https://regist.netkeiba.com/account/?pid=login"
LOGIN_POST = "https://regist.netkeiba.com/account/"
RACE_LIST = "https://race.netkeiba.com/top/race_list_sub.html?kaisai_date={date}"
SHUTUBA = "https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
SLEEP_SEC = 2.0
TIMEOUT = 25

_COURSE_NAME = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
    "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉",
}
_HORSE_ROW_RE = re.compile(r'<tr class="HorseList.*?</tr>', re.DOTALL)


def login() -> requests.Session:
    """netkeiba にログインしたセッションを返す。"""
    uid = os.environ.get("NETKEIBA_USER_ID")
    pw = os.environ.get("NETKEIBA_PASSWORD")
    if not uid or not pw:
        raise SystemExit("NETKEIBA_USER_ID / NETKEIBA_PASSWORD が .env にありません")
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "ja-JP,ja"})
    s.get(LOGIN_PAGE, timeout=TIMEOUT)
    s.post(
        LOGIN_POST,
        data={"pid": "login", "action": "auth", "return_url2": "", "mem_tp": "",
              "login_id": uid, "pswd": pw},
        headers={"Referer": LOGIN_PAGE},
        timeout=TIMEOUT,
    )
    if "nkauth" not in s.cookies:
        raise SystemExit("ログイン失敗（nkauth Cookie が取得できない）")
    logger.info("ログイン成功 cookies=%s", list(s.cookies.keys()))
    return s


def list_race_ids(s: requests.Session, date: str) -> list[str]:
    """開催日 YYYYMMDD の全 race_id（12桁）を返す。"""
    r = s.get(RACE_LIST.format(date=date), timeout=TIMEOUT)
    html = r.content.decode("euc-jp", errors="replace")
    return sorted(set(re.findall(r"race_id=(\d{12})", html)))


def _strip(t: str) -> str:
    return re.sub(r"<[^>]+>", "", t).strip()


def parse_shutuba(html: str) -> dict:
    """想定出馬表をパースして {race_name, surface, distance, horses:[...]} を返す。"""
    title = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
    title = re.sub(r"\s+", " ", title.group(1)).strip() if title else ""
    race_name = title.split("出馬表")[0].strip() if "出馬表" in title else title
    # 距離・馬場（RaceData01 の "ダ1400m" / "芝2000m" / "障3000m"）
    surface = distance = None
    rd = re.search(r'<div class="RaceData01">(.*?)</div>', html, re.DOTALL)
    sd = re.search(r"(芝|ダ|障)\s*(\d{3,4})\s*m", rd.group(1) if rd else html)
    if sd:
        surface = sd.group(1)
        distance = int(sd.group(2))
    horses = []
    for row in _HORSE_ROW_RE.findall(html):
        hid = re.search(r"db\.netkeiba\.com/horse/(\d+)", row)
        # 馬名: HorseName span 内 <a title="..">
        nm = re.search(r'class="HorseName">\s*<a[^>]*title="([^"]+)"', row)
        if not nm:
            nm = re.search(r"/horse/\d+[^>]*>\s*([^<]+?)\s*<", row)
        sexage = re.search(r'<td class="Barei[^"]*"[^>]*>([^<]+)</td>', row)
        # 想定騎手: Jockey セル内の <a> テキスト（無ければ None）
        jk_cell = re.search(r'<td class="Jockey"[^>]*>(.*?)</td>', row, re.DOTALL)
        jockey = None
        if jk_cell:
            jt = _strip(jk_cell.group(1))
            # 「○○」「未定」等は想定騎手未確定のプレースホルダ → None
            if jt and jt not in ("未定", "想定なし") and not re.fullmatch(r"[○◯〇△▲\s]+", jt):
                jockey = jt
        name = nm.group(1).strip() if nm else None
        if not name:
            continue
        horses.append({
            "netkeiba_horse_id": hid.group(1) if hid else None,
            "horse_name": name,
            "sex_age": sexage.group(1).strip() if sexage else None,
            "expected_jockey": jockey,
        })
    return {"race_name": race_name, "surface": surface, "distance": distance,
            "horses": horses}


UPSERT_SQL = """
INSERT INTO keiba.projected_entries
  (netkeiba_race_id, race_date, course_code, race_number, race_name,
   netkeiba_horse_id, horse_name, sex_age, expected_jockey_name, updated_at)
VALUES %s
ON CONFLICT (netkeiba_race_id, horse_name) DO UPDATE SET
  race_name = EXCLUDED.race_name,
  netkeiba_horse_id = EXCLUDED.netkeiba_horse_id,
  sex_age = EXCLUDED.sex_age,
  expected_jockey_name = EXCLUDED.expected_jockey_name,
  updated_at = now()
"""


# keiba.races へ出馬表確定前の placeholder 行を作る（一覧/詳細で引けるように）。
# TOKU 取込と同じ ON CONFLICT(jravan_race_id) パターン。確定 RA 取込で上書きされるよう
# 既存行は触らない（DO NOTHING）。sekito.v_races（keiba.races のビュー）にも自動反映される。
RACES_SQL = """
INSERT INTO keiba.races
  (jravan_race_id, date, course, course_name, race_number, race_name,
   surface, distance, registered_count)
VALUES %s
ON CONFLICT (jravan_race_id) DO NOTHING
"""


# sekito(POG出走予定)は keiba.special_registrations を読むため、想定馬をここにも登録する。
# source='netkeiba' で TOKU(特別登録)と区別。既存 TOKU 行は触らない(DO NOTHING)。
# jravan_horse_code には血統登録番号(=netkeiba_horse_id, 10桁)を入れる。
SPECIAL_SQL = """
INSERT INTO keiba.special_registrations
  (jravan_race_id, race_date, course_code, race_number,
   jravan_horse_code, horse_name, sex, age, race_name, source, updated_at)
VALUES %s
ON CONFLICT ON CONSTRAINT uq_special_reg_race_horse DO NOTHING
"""


def jravan_from(date: str, netkeiba_race_id: str) -> str:
    """race_date(YYYYMMDD) + netkeiba_id(YYYY+CC+KK+DD+RR) → jravan_race_id(16)。"""
    return date + netkeiba_race_id[4:]


def _split_sex_age(sex_age: str | None) -> tuple[str | None, int | None]:
    """"牝3" → ("牝", 3)。"""
    if not sex_age:
        return None, None
    m = re.match(r"^(牡|牝|セ|騸|せん)?\s*(\d+)?", sex_age)
    if not m:
        return None, None
    sex = m.group(1)
    age = int(m.group(2)) if m.group(2) else None
    return sex, age


def _dsn() -> str:
    return (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )


def save_db(results: list[dict]) -> tuple[int, int, int]:
    """想定馬を projected_entries / special_registrations に、レースを races に UPSERT。

    戻り値: (想定馬行数, placeholder races 数, special_registrations 投入行数)
    """
    horse_rows = []
    race_rows = []
    special_rows = []
    for r in results:
        if not r["horses"]:
            continue
        jid = jravan_from(r["date"], r["netkeiba_race_id"])
        race_rows.append((
            jid, r["date"], r["course_code"], r["course_name"], r["race_number"],
            r["race_name"], r.get("surface"), r.get("distance"), len(r["horses"]),
        ))
        for h in r["horses"]:
            horse_rows.append((
                r["netkeiba_race_id"], r["date"], r["course_code"], r["race_number"],
                r["race_name"], h["netkeiba_horse_id"], h["horse_name"],
                h["sex_age"], h["expected_jockey"],
            ))
            # special_registrations は血統登録番号(=netkeiba_horse_id)が必須
            if h["netkeiba_horse_id"]:
                sex, age = _split_sex_age(h["sex_age"])
                special_rows.append((
                    jid, r["date"], r["course_code"], r["race_number"],
                    h["netkeiba_horse_id"], h["horse_name"], sex, age,
                    r["race_name"], "netkeiba",
                ))
    if not horse_rows:
        return 0, 0, 0
    conn = psycopg2.connect(_dsn())
    with conn, conn.cursor() as cur:
        if race_rows:
            execute_values(cur, RACES_SQL, race_rows,
                           template="(%s,%s,%s,%s,%s,%s,%s,%s,%s)")
        execute_values(cur, UPSERT_SQL, horse_rows,
                       template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,now())")
        if special_rows:
            execute_values(cur, SPECIAL_SQL, special_rows,
                           template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())")
    conn.close()
    return len(horse_rows), len(race_rows), len(special_rows)


def next_weekend() -> list[str]:
    """次に来る土曜・日曜の YYYYMMDD を返す（今日が土日でも翌週末）。"""
    from datetime import date, timedelta
    today = date.today()
    # 次の土曜（today より後の最初の土曜）
    days_to_sat = (5 - today.weekday()) % 7
    if days_to_sat == 0:
        days_to_sat = 7
    sat = today + timedelta(days=days_to_sat)
    sun = sat + timedelta(days=1)
    return [sat.strftime("%Y%m%d"), sun.strftime("%Y%m%d")]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dates", default=None,
                   help="対象日 YYYYMMDD（カンマ区切り）。省略時は次の土日を自動算出")
    p.add_argument("--limit", type=int, default=None, help="処理レース数上限（テスト用）")
    p.add_argument("--out", default=None, help="JSON 出力先パス（任意）")
    p.add_argument("--dry-run", action="store_true", help="DB 保存をスキップ")
    args = p.parse_args()

    s = login()
    dates = ([d.strip() for d in args.dates.split(",") if d.strip()]
             if args.dates else next_weekend())
    logger.info("対象日: %s", ",".join(dates))
    results: list[dict] = []
    n_race = 0

    for date in dates:
        rids = list_race_ids(s, date)
        logger.info("%s: %d レース", date, len(rids))
        for rid in rids:
            if args.limit and n_race >= args.limit:
                break
            n_race += 1
            try:
                html = s.get(SHUTUBA.format(race_id=rid), timeout=TIMEOUT).content.decode(
                    "euc-jp", errors="replace")
            except Exception as e:  # noqa: BLE001
                logger.error("  %s fetch失敗: %s", rid, e)
                time.sleep(SLEEP_SEC)
                continue
            parsed = parse_shutuba(html)
            cc = rid[4:6]
            rec = {
                "date": date,
                "netkeiba_race_id": rid,
                "course_code": cc,
                "course_name": _COURSE_NAME.get(cc, cc),
                "race_number": int(rid[10:12]),
                **parsed,
            }
            results.append(rec)
            n_jk = sum(1 for h in parsed["horses"] if h["expected_jockey"])
            logger.info("  %s %sR %s: 想定%d頭(騎手%d)",
                        rec["course_name"], rec["race_number"], rec["race_name"],
                        len(parsed["horses"]), n_jk)
            time.sleep(SLEEP_SEC)

    total_h = sum(len(r["horses"]) for r in results)
    logger.info("取得完了: %d レース / 想定 %d 頭", len(results), total_h)

    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2))
        logger.info("JSON 出力: %s", args.out)

    if args.dry_run:
        logger.info("--dry-run のため DB 保存はスキップ")
    else:
        n_h, n_r, n_s = save_db(results)
        logger.info(
            "DB 保存: projected_entries %d 行 / races placeholder %d 件 / "
            "special_registrations(netkeiba) %d 行", n_h, n_r, n_s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
