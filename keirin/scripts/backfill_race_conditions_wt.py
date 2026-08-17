#!/usr/bin/env python3
"""レースごとの実測走路条件（天候・風速）を winticket から backfill する。

## なぜ新設したか

波乱条件の仮説「普段と違う環境（雨走路・強風）で荒れる」を検証しようとしたところ、
気象データが**一つも無い**ことが分かった（2026-08-18）:

  - `keirin.wt_weather` は **0 行**。書き込み側の `scripts/collect_weather.py` は
    ローカル SQLite (`data/keirin.db`) にしか書かず、2026-07-22 の PG 一本化で
    取り残されていた
  - そもそも Open-Meteo の会場×時刻グリッド**推定値**であって、走路の実測ではない

winticket が開催単位の JSON API で**競輪場発表の実測値**を出しているので、そちらを取る。

    GET https://api.winticket.jp/v1/keirin/cups/{cupId}
      → {"schedules": [{"id","date",...}], "races": [{"scheduleId","number",
                                                      "weather","windSpeed",...}]}

**1リクエストで開催の全レース**（3日開催なら21レース）が返るので、
101,622レースを 3,306 リクエストで賄える。レース単位で引くと約39時間かかる。

## 🔴 未確定レースを取り込まないこと

winticket は**発走前のレースに `weather=''` / `windSpeed='0.0'` を返す**
（2026-08-18 07:24 実測。当日の全レースがこの状態だった）。この `'0.0'` は
無風の観測ではなく**ただのプレースホルダ**で、取り込むと「無風の日」を大量に捏造する。
`decidedAt` があるレースだけ書く。

## 保存先と、朝の値について

`keirin.wt_race_conditions` の**実測列**（`weather` / `wind_speed` / `settled_at`）。
`wt_weather` とは別物なので混同しないこと。

⚠️ 本スクリプトが埋めるのは**発走時点の実測**だけ。**実績の検証にはこれを使う**。
   予想へ入れる「朝時点で知り得た値」は winticket からは取れない（朝は空）ので、
   `backfill_race_forecast.py` が Open-Meteo の予報を `fc_*` 列へ入れる。
   **この2系統を混ぜて使わないこと**（2026-08-18 ユーザー指示）。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/backfill_race_conditions_wt.py \\
        [--since 2022-12-01] [--limit N] [--sleep 1.0] [--dry-run] [--refresh]
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import get_connection  # noqa: E402

API = "https://api.winticket.jp/v1/keirin/cups/{cup_id}"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def _wind(v) -> float | None:
    """'2.0' / '' / None → float | None。

    🔴 **空文字を 0.0 にしてはいけない。** 無風(0.0)と未発表を混ぜると
       「風速0の日が異常に多い」データになり、風の検証が最初から壊れる。
    """
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _settled_at(v) -> int | None:
    """winticket の decidedAt（UNIX秒）。無ければ未確定。"""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _weather(v) -> str | None:
    """'晴れ' / '' / 0 → str | None（空・非文字列は未発表として None）。"""
    if v is None or isinstance(v, (int, float)):
        return None
    s = str(v).strip()
    return s or None


def fetch_cup(session: requests.Session, cup_id: str,
              venue_id: str) -> list[dict]:
    """開催の全レースの (race_key, weather, wind_speed) を返す。"""
    r = session.get(API.format(cup_id=cup_id), timeout=20,
                    headers={"User-Agent": UA})
    r.raise_for_status()
    d = r.json()
    # scheduleId → 日付。races[].scheduleId から race_key を組む。
    dates = {s["id"]: str(s["date"]) for s in (d.get("schedules") or [])
             if s.get("id") and s.get("date")}
    out: list[dict] = []
    for rc in (d.get("races") or []):
        date = dates.get(rc.get("scheduleId"))
        no = rc.get("number")
        if not date or not no:
            continue
        # 🔴 未確定レースは弾く。`windSpeed='0.0'` は観測ではなくプレースホルダで、
        #    取り込むと「無風」が捏造される（2026-08-18 に実測で確認）。
        settled = _settled_at(rc.get("decidedAt"))
        if settled is None:
            continue
        w, ws = _weather(rc.get("weather")), _wind(rc.get("windSpeed"))
        if w is None and ws is None:
            continue                    # 両方無いなら書かない（空行を作らない）
        out.append({
            "race_key": f"{date}_{venue_id}_{int(no):02d}",
            "weather": w, "wind_speed": ws,
            "settled_at": datetime.fromtimestamp(settled, tz=timezone.utc)
                          .isoformat(),
        })
    return out


def target_cups(since: str, refresh: bool) -> list[tuple[str, str]]:
    """(cup_id, venue_id) の一覧。既に全レース埋まっている開催は既定で飛ばす。"""
    q = """
        SELECT r.cup_id, min(r.venue_id) AS venue_id,
               count(*) AS n_races,
               count(c.race_key) AS n_have
        FROM wt_races r
        LEFT JOIN wt_race_conditions c
               ON c.race_key = r.race_key AND c.settled_at IS NOT NULL
        WHERE r.cancel = 0 AND r.race_date >= ?
        GROUP BY r.cup_id
        ORDER BY r.cup_id
    """
    with get_connection() as conn:
        rows = [(r[0], str(r[1]), int(r[2]), int(r[3]))
                for r in conn.execute(q, (since,))]
    if refresh:
        return [(c, v) for c, v, _n, _h in rows]
    # 🔴 「1件でもあれば済み」にしない。開催途中まで取れて中断した場合に
    #    残りが永久に埋まらなくなる。全レース揃っている開催だけ飛ばす。
    return [(c, v) for c, v, n, h in rows if h < n]


def upsert(rows: list[dict]) -> int:
    if not rows:
        return 0
    with get_connection() as conn:
        # 🔴 INSERT OR REPLACE にしない。予報列（fc_*）を別スクリプトが埋めるので、
        #    行ごと置換すると**そちらを消してしまう**。実測列だけ更新する。
        conn.executemany(
            "INSERT INTO wt_race_conditions "
            "(race_key, weather, wind_speed, settled_at) "
            "VALUES (:race_key, :weather, :wind_speed, :settled_at) "
            "ON CONFLICT (race_key) DO UPDATE SET "
            "  weather = EXCLUDED.weather, wind_speed = EXCLUDED.wind_speed, "
            "  settled_at = EXCLUDED.settled_at", rows)
        conn.commit()
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2022-12-01")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--refresh", action="store_true",
                    help="取得済みの開催も引き直す（当日分の更新用）")
    args = ap.parse_args()

    cups = target_cups(args.since, args.refresh)
    if args.limit:
        cups = cups[:args.limit]
    print(f"[conditions] 対象 {len(cups)} 開催 (since={args.since})", flush=True)

    s = requests.Session()
    total = n_fail = 0
    for i, (cup_id, venue_id) in enumerate(cups, 1):
        try:
            rows = fetch_cup(s, cup_id, venue_id)
        except Exception as e:                          # noqa: BLE001
            n_fail += 1
            print(f"  [{i}/{len(cups)}] {cup_id}: 失敗 {e}", flush=True)
            time.sleep(args.sleep)
            continue
        total += len(rows) if args.dry_run else upsert(rows)
        if i % 50 == 0 or i == len(cups):
            print(f"  [{i}/{len(cups)}] {cup_id} 累計 {total:,}レース "
                  f"失敗{n_fail}", flush=True)
        time.sleep(args.sleep)

    print(f"[conditions] {'(dry-run) ' if args.dry_run else ''}"
          f"{total:,}レース / 失敗 {n_fail} 開催")
    if not args.dry_run:
        with get_connection() as conn:
            for r in conn.execute(
                    # ⚠️ 別名を付けること。`_PgRow` は同名列（全部 count）を
                    #    畳んでしまい IndexError になる。
                    "SELECT count(*) AS n_all, count(weather) AS n_w, "
                    "       count(wind_speed) AS n_ws, "
                    "       count(settled_at) AS n_s FROM wt_race_conditions"):
                print(f"[conditions] wt_race_conditions 総数 {r[0]:,} "
                      f"/ 天候あり {r[1]:,} / 風速あり {r[2]:,} "
                      f"/ 確定 {r[3]:,}")


if __name__ == "__main__":
    main()
