"""メカウチダの買い目 × 指数上位5位 / 穴ぐさ 一致の Discord 通知（10 分おき cron）。

判定は ``src/services/mekauchida_notify.py``、パーサは ``src/scrapers/mekauchida.py``。

1. 今日の中央のレースのうち、発走 1 時間前〜発走前のものを DB から取る。
   無ければサイトを見ずに終わる（＝開催日でなければ何もしない）
2. サイトの今日ページを 1 回だけ取得し、監視中のレースの買い目を記録する
   （``logs/mekauchida_snapshots.jsonl``。見込み→判断の変化を後から測るため）
3. 発走まで 10 分以内（＝発走前の最後の監視）のレースで、買い目が
   指数 5 位以内 または 穴ぐさ と一致した馬を Discord に送る。
   送ったレースは ``logs/mekauchida_notified.json`` に残し二重送信しない

使い方:
    .venv/bin/python scripts/mekauchida_discord_notify.py
    .venv/bin/python scripts/mekauchida_discord_notify.py --dry-run
    .venv/bin/python scripts/mekauchida_discord_notify.py --now "2026-10-03 15:40" --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

try:
    from dotenv import load_dotenv

    load_dotenv(_root.parent / ".env")
except ImportError:
    pass  # Docker コンテナ内では環境変数が env_file で注入済み

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from src.db.session import sync_engine as engine  # noqa: E402
from src.indices.composite import COMPOSITE_VERSION  # noqa: E402
from src.scrapers.mekauchida import SITE_URL, MekauchidaParseError, parse_today_page  # noqa: E402
from src.services.mekauchida_notify import (  # noqa: E402
    JST,
    HorseContext,
    MatchedPick,
    build_message,
    is_final_poll,
    is_monitoring,
    match_race,
    parse_post_at,
)
from src.utils.discord import send  # noqa: E402

# コンテナでは /app/logs（ホストの logs/ をマウント）。ローカルではリポジトリ直下の logs/
LOG_DIR = Path("/app/logs") if Path("/app/logs").is_dir() else _root.parent / "logs"
STATE_FILE = LOG_DIR / "mekauchida_notified.json"
SNAPSHOT_FILE = LOG_DIR / "mekauchida_snapshots.jsonl"

RACES_SQL = text("""
SELECT id, course_name, race_number, post_time
FROM keiba.races
WHERE date = :d AND post_time IS NOT NULL
""")

# 指数順位は API（races.py）と同じく「レースの最新 version を COMPOSITE_VERSION で上限」
# の行を使い、出走取消・発走除外（abnormality_code 1/2）を母集団から外す
HORSES_SQL = text("""
WITH rv AS (
  SELECT race_id, LEAST(MAX(version), :cv) AS v
  FROM keiba.calculated_indices
  WHERE race_id = ANY(:race_ids)
  GROUP BY race_id
),
ranked AS (
  SELECT ci.race_id, re.horse_number, ci.composite_index,
         RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.composite_index DESC NULLS LAST) AS idx_rank
  FROM keiba.calculated_indices ci
  JOIN rv ON rv.race_id = ci.race_id AND rv.v = ci.version
  JOIN keiba.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
  LEFT JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
  WHERE COALESCE(rr.abnormality_code, 0) NOT IN (1, 2)
    AND ci.composite_index IS NOT NULL
)
SELECT r.id AS race_id, re.horse_number, k.idx_rank, k.composite_index, a.rank AS anagusa_rank
FROM keiba.races r
JOIN keiba.race_entries re ON re.race_id = r.id
LEFT JOIN ranked k ON k.race_id = r.id AND k.horse_number = re.horse_number
LEFT JOIN sekito.anagusa a
  ON a.date = to_date(r.date, 'YYYYMMDD')
 AND a.race_no = r.race_number
 AND a.horse_no = re.horse_number
 AND a.course_code = CASE r.course
       WHEN '01' THEN 'JSPK' WHEN '02' THEN 'JHKD' WHEN '03' THEN 'JFKS'
       WHEN '04' THEN 'JNGT' WHEN '05' THEN 'JTOK' WHEN '06' THEN 'JNKY'
       WHEN '07' THEN 'JCKO' WHEN '08' THEN 'JKYO' WHEN '09' THEN 'JHSN'
       WHEN '10' THEN 'JKKR' END
LEFT JOIN keiba.race_results rr2 ON rr2.race_id = r.id AND rr2.horse_id = re.horse_id
WHERE r.id = ANY(:race_ids)
  AND COALESCE(rr2.abnormality_code, 0) NOT IN (1, 2)  -- 取消・除外馬は穴ぐさ一致でも送らない
""")


def log(msg: str) -> None:
    """標準出力へ時刻付きで書く（cron 側でログファイルへリダイレクト）。"""
    print(f"{datetime.now(JST):%Y-%m-%d %H:%M:%S} [mekauchida] {msg}", flush=True)


def fetch_page(retries: int) -> str:
    """サイトの今日ページを取得する（失敗時は ``retries`` 回まで 20 秒おきに再試行）。"""
    req = urllib.request.Request(SITE_URL, headers={"User-Agent": "kiseki-monitor/1.0"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read().decode("utf-8")
        except Exception as e:  # noqa: BLE001 — 取得失敗は種類を問わず再試行
            if attempt >= retries:
                raise
            log(f"WARN: 取得失敗（再試行します）: {e}")
            time.sleep(20)
    raise RuntimeError("unreachable")


def load_state(date: str) -> set[str]:
    """今日すでに通知したレース（``場名R``）を読む。日付が変われば空。"""
    try:
        data = json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return set()
    return set(data.get("races", [])) if data.get("date") == date else set()


def save_state(date: str, races: set[str]) -> None:
    """通知済みレースを書き出す。"""
    STATE_FILE.write_text(json.dumps({"date": date, "races": sorted(races)}, ensure_ascii=False))


def main() -> int:
    """1 回分の監視。終了コード 0=正常 1=異常。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Discord に送らず標準出力のみ")
    parser.add_argument("--now", help="現在時刻を上書き（検証用・JST 'YYYY-MM-DD HH:MM'）")
    args = parser.parse_args()

    now = datetime.strptime(args.now, "%Y-%m-%d %H:%M").replace(tzinfo=JST) if args.now else datetime.now(JST)
    today = now.strftime("%Y%m%d")

    with Session(engine) as db:
        races = db.execute(RACES_SQL, {"d": today}).fetchall()
    monitored = {
        (r.course_name, r.race_number): r for r in races if is_monitoring(parse_post_at(today, r.post_time), now)
    }
    if not monitored:
        return 0  # 開催日でない・監視時間外。毎回出るのでログに書かない

    final_keys = {k for k, r in monitored.items() if is_final_poll(parse_post_at(today, r.post_time), now)}

    try:
        page = parse_today_page(fetch_page(retries=1 if final_keys else 0), year=now.year)
    except MekauchidaParseError as e:
        log(f"ERROR: ページを読めない: {e}")
        return 1
    except Exception as e:  # noqa: BLE001
        log(f"ERROR: 取得失敗: {e}")
        return 1
    if page.date != today:
        log(f"ERROR: サイトの日付が今日ではない（site={page.date} today={today}）。通知しない")
        return 1

    site_races = {(r.venue, r.race_number): r for r in page.races}
    missing = [f"{v}{n}R" for (v, n) in monitored if (v, n) not in site_races]
    if missing:
        log(f"WARN: サイトに無いレース: {','.join(missing)}")

    race_ids = [r.id for r in monitored.values()]
    with Session(engine) as db:
        rows = db.execute(HORSES_SQL, {"race_ids": race_ids, "cv": COMPOSITE_VERSION}).fetchall()
    horses: dict[int, dict[int, HorseContext]] = {}
    for row in rows:
        horses.setdefault(row.race_id, {})[row.horse_number] = HorseContext(
            horse_number=row.horse_number,
            index_rank=row.idx_rank,
            composite_index=float(row.composite_index) if row.composite_index is not None else None,
            anagusa_rank=row.anagusa_rank,
        )

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    notified = load_state(today)
    to_send: list[MatchedPick] = []
    with SNAPSHOT_FILE.open("a") as snap:
        for key, db_race in sorted(monitored.items(), key=lambda kv: kv[1].post_time):
            site = site_races.get(key)
            if site is None:
                continue
            race_horses = horses.get(db_race.id, {})
            matches = match_race(site, race_horses)
            final = key in final_keys
            if final and not any(h.index_rank is not None for h in race_horses.values()):
                # 「一致なし」と「指数が算出されていない」を区別する
                log(f"WARN: {key[0]}{key[1]}R の指数が DB に無い（穴ぐさ一致だけで判定）")
            snap.write(
                json.dumps(
                    {
                        "at": now.isoformat(timespec="seconds"),
                        "site_updated": page.updated,
                        "race": f"{key[0]}{key[1]}R",
                        "post": db_race.post_time,
                        "state": site.state,
                        "final": final,
                        "picks": [
                            {
                                "no": p.horse_number,
                                "kind": p.kind,
                                "flags": list(p.flags),
                                "ev": p.expected_value,
                            }
                            for p in site.picks
                        ],
                        "matched": [m.pick.horse_number for m in matches],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            label = f"{key[0]}{key[1]}R"
            if final and matches and label not in notified:
                to_send.extend(matches)
                notified.add(label)

    if not to_send:
        if final_keys:
            log(f"最終監視 {len(final_keys)}R: 一致なし")
        return 0

    message = build_message(to_send, now)
    print(message)
    if args.dry_run:
        log("--dry-run のため送信しない")
        return 0
    if not send(message):
        log("ERROR: Discord 送信失敗")
        return 1
    save_state(today, notified)
    log(f"送信: {len(to_send)}頭")
    return 0


if __name__ == "__main__":
    sys.exit(main())
