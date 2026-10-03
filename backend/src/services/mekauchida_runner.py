"""メカウチダ一致通知の 1 回分の監視（中央・地方で共通の実行ループ）。

判定は ``mekauchida_notify``（純関数）、パーサは ``scrapers.mekauchida``。
ここは DB・HTTP・ファイル・Discord の I/O を受け持つ。中央と地方の違いは
``MonitorConfig``（サイト URL・SQL・ファイル名・見出し）だけにしてあり、
🔴 **監視窓・二重送信防止・送信の処理を各スクリプトに写さないこと**
（写すと片方だけ直る型のバグになる）。

1. 今日のレースのうち、発走 1 時間前〜発走前のものを DB から取る。
   無ければサイトを見ずに終わる（＝開催日でなければ何もしない）
2. サイトを 1 回だけ取得し、監視中のレースの買い目を記録する（snapshot）
3. 発走まで 10 分以内（＝発走前の最後の監視）のレースで一致した馬を送る。
   送ったレースは state に残し二重送信しない
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import TextClause
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from src.scrapers.mekauchida import MekauchidaParseError, parse_today_page
from src.services.mekauchida_notify import (
    JST,
    HorseContext,
    MatchedPick,
    build_message,
    is_final_poll,
    is_monitoring,
    match_race,
    parse_post_at,
)

MENTION = "@everyone"
"""通知の先頭に付けるメンション（オフラインの端末にもプッシュ通知を鳴らすため @everyone）。

Webhook は ``allowed_mentions`` を省くと @here / @everyone を解釈する。
"""


def with_mention(message: str, mention: str) -> str:
    """本文の先頭にメンション行を付ける（空なら付けない）。"""
    return f"{mention}\n{message}" if mention else message


@dataclass(frozen=True)
class MonitorConfig:
    """中央・地方ごとの設定。

    ``races_sql`` は ``:d``（YYYYMMDD）を受け ``id, course_name, race_number, post_time`` を返す。
    ``horses_sql`` は ``:race_ids`` と ``sql_params`` を受け
    ``race_id, horse_number, idx_rank, composite_index, anagusa_rank`` を返す
    （取消・除外馬は返さないこと）。
    """

    name: str  # ログの接頭辞
    site_url: str
    races_sql: TextClause
    horses_sql: TextClause
    state_file: Path
    snapshot_file: Path
    title: str  # 通知の見出し
    sql_params: dict[str, Any] = field(default_factory=dict)
    mention: str = MENTION


def _log(cfg: MonitorConfig, msg: str) -> None:
    print(f"{datetime.now(JST):%Y-%m-%d %H:%M:%S} [{cfg.name}] {msg}", flush=True)


def fetch_html(url: str, retries: int, on_retry: Callable[[str], None]) -> str:
    """ページを取得する（失敗時は ``retries`` 回まで 20 秒おきに再試行）。"""
    req = urllib.request.Request(url, headers={"User-Agent": "kiseki-monitor/1.0"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return str(resp.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 — 取得失敗は種類を問わず再試行
            if attempt >= retries:
                raise
            on_retry(f"WARN: 取得失敗（再試行します）: {e}")
            time.sleep(20)
    raise RuntimeError("unreachable")


def _load_state(path: Path, date: str) -> set[str]:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return set()
    return set(data.get("races", [])) if data.get("date") == date else set()


def _save_state(path: Path, date: str, races: set[str]) -> None:
    path.write_text(json.dumps({"date": date, "races": sorted(races)}, ensure_ascii=False))


def run_once(
    cfg: MonitorConfig,
    engine: Engine,
    now: datetime,
    dry_run: bool,
    send: Callable[[str], bool],
) -> int:
    """1 回分の監視。終了コード 0=正常 1=異常。"""
    today = now.strftime("%Y%m%d")

    with Session(engine) as db:
        races = db.execute(cfg.races_sql, {"d": today}).fetchall()
    monitored = {
        (r.course_name, r.race_number): r for r in races if is_monitoring(parse_post_at(today, r.post_time), now)
    }
    if not monitored:
        return 0  # 開催日でない・監視時間外。毎回出るのでログに書かない

    final_keys = {k for k, r in monitored.items() if is_final_poll(parse_post_at(today, r.post_time), now)}

    try:
        html = fetch_html(cfg.site_url, 1 if final_keys else 0, lambda m: _log(cfg, m))
        page = parse_today_page(html, year=now.year)
    except MekauchidaParseError as e:
        _log(cfg, f"ERROR: ページを読めない: {e}")
        return 1
    except Exception as e:  # noqa: BLE001
        _log(cfg, f"ERROR: 取得失敗: {e}")
        return 1
    if page.date != today:
        _log(cfg, f"ERROR: サイトの日付が今日ではない（site={page.date} today={today}）。通知しない")
        return 1

    site_races = {(r.venue, r.race_number): r for r in page.races}
    site_venues = {r.venue for r in page.races}
    # サイトが扱っていない場（例: ばんえい）は黙る。場はあるのにレースが無いときだけ警告する
    missing = [f"{v}{n}R" for (v, n) in monitored if v in site_venues and (v, n) not in site_races]
    if missing:
        _log(cfg, f"WARN: サイトに無いレース: {','.join(missing)}")

    race_ids = [r.id for r in monitored.values()]
    with Session(engine) as db:
        rows = db.execute(cfg.horses_sql, {"race_ids": race_ids, **cfg.sql_params}).fetchall()
    horses: dict[int, dict[int, HorseContext]] = {}
    for row in rows:
        horses.setdefault(row.race_id, {})[row.horse_number] = HorseContext(
            horse_number=row.horse_number,
            index_rank=row.idx_rank,
            composite_index=float(row.composite_index) if row.composite_index is not None else None,
            anagusa_rank=row.anagusa_rank,
        )

    cfg.state_file.parent.mkdir(parents=True, exist_ok=True)
    notified = _load_state(cfg.state_file, today)
    to_send: list[MatchedPick] = []
    with cfg.snapshot_file.open("a") as snap:
        for key, db_race in sorted(monitored.items(), key=lambda kv: kv[1].post_time):
            site = site_races.get(key)
            if site is None:
                continue
            race_horses = horses.get(db_race.id, {})
            matches = match_race(site, race_horses)
            final = key in final_keys
            label = f"{key[0]}{key[1]}R"
            if final and not any(h.index_rank is not None for h in race_horses.values()):
                # 「一致なし」と「指数が算出されていない」を区別する
                _log(cfg, f"WARN: {label} の指数が DB に無い")
            snap.write(
                json.dumps(
                    {
                        "at": now.isoformat(timespec="seconds"),
                        "site_updated": page.updated,
                        "race": label,
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
            if final and matches and label not in notified:
                to_send.extend(matches)
                notified.add(label)

    if not to_send:
        if final_keys:
            _log(cfg, f"最終監視 {len(final_keys)}R: 一致なし")
        return 0

    message = with_mention(build_message(to_send, now, cfg.title), cfg.mention)
    print(message)
    if dry_run:
        _log(cfg, "--dry-run のため送信しない")
        return 0
    if not send(message):
        _log(cfg, "ERROR: Discord 送信失敗")
        return 1
    _save_state(cfg.state_file, today, notified)
    _log(cfg, f"送信: {len(to_send)}頭")
    return 0
