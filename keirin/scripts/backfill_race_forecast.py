#!/usr/bin/env python3
"""レース発走時刻の**予報**（朝時点で知り得た値）を Open-Meteo から backfill する。

## なぜ実測と別に要るのか（2026-08-18 ユーザー指示）

推奨は**朝**に作るが、天候は発走までに変わる。したがって

  - **実績の検証**は「発走時点の実測」で見ないと誤った結論になる
    → `backfill_race_conditions_wt.py`（winticket・`weather` / `wind_speed`）
  - **予想への投入**は「朝時点で知り得た値」＝予報でないと本番で使えない
    → 本スクリプト（`fc_*` 列）

🔴 **この2系統を1つの列に混ぜてはいけない。** 混ぜると、検証では効いて見えるのに
   配信では欠損する特徴量が出来上がる（地方競馬 v14 の市場特徴で実際に踏んだ型）。

🔴 winticket は朝の時点では天候を出していない（2026-08-18 07:24 実測で当日全レースが
   `weather=''` / `windSpeed='0.0'`）。朝の値は winticket からは取れない。

## 出所

Open-Meteo **Historical Forecast API**（`historical-forecast-api.open-meteo.com`）。
実測解析ではなく**当時アーカイブされた予報**を返すので、「朝に知り得たか」を満たす。
2022-12-05 まで遡れることを実測で確認済み（本リポジトリのデータ開始と同じ）。

`wind_direction_10m` が取れるのが重要。既存の風検証（G06 / `exp_wind_wt.py`）は
wind_dir を DB から読みながら**特徴量に入れておらず**、「競輪場×向き×強さ」は
一度も検証されていない。向きを落とすと逆向きの効果が打ち消し合うため、
効いていても AUC 差 0 に見える。

⚠️ 当日分（まだアーカイブに無い日）は通常の Forecast API へ自動で切り替える。
   どちらを使ったかは `fc_source` に残す。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/backfill_race_forecast.py \\
        [--since 2022-12-01] [--until 2026-08-17] [--sleep 1.0] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from datetime import date as _date
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import get_connection  # noqa: E402
from src.scraper.weather import VENUE_COORDS  # noqa: E402

HIST = "https://historical-forecast-api.open-meteo.com/v1/forecast"
FCST = "https://api.open-meteo.com/v1/forecast"
HOURLY = "wind_speed_10m,wind_direction_10m,precipitation,weather_code"
JST = timezone(timedelta(hours=9))
# Open-Meteo は既定で km/h を返す。競輪の発表は m/s なので合わせる。
KMH_TO_MS = 1000.0 / 3600.0


def race_hours(since: str, until: str) -> dict[str, list[tuple[str, str]]]:
    """{venue_id: [(race_key, 'YYYY-MM-DDTHH:00' JST), ...]}。

    発走時刻の**時**へ丸める（予報は毎時値）。`start_at` が無いレースは落とす
    （時刻不明のまま近い時刻を当てると、荒れやすい最終レースに朝の値を当てる等の
      ずれ方をする）。
    """
    out: dict[str, list[tuple[str, str]]] = defaultdict(list)
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT race_key, venue_id, start_at FROM wt_races "
            "WHERE cancel = 0 AND race_date BETWEEN ? AND ? "
            "  AND start_at IS NOT NULL", (since, until))
        for rk, vid, start_at in rows:
            hour = _to_hour(start_at)
            if hour:
                out[str(vid)].append((rk, hour))
    return dict(out)


def _to_hour(start_at) -> str | None:
    """`wt_races.start_at` を JST の 'YYYY-MM-DDTHH:00' へ。

    UNIX 秒（winticket 由来）と ISO 文字列の両方が入りうるので両対応する。
    """
    if start_at is None:
        return None
    s = str(start_at).strip()
    if not s:
        return None
    try:                                    # UNIX 秒
        return (datetime.fromtimestamp(int(float(s)), tz=JST)
                .strftime("%Y-%m-%dT%H:00"))
    except (ValueError, OSError):
        pass
    try:                                    # ISO 文字列
        return (datetime.fromisoformat(s.replace("Z", "+00:00"))
                .astimezone(JST).strftime("%Y-%m-%dT%H:00"))
    except ValueError:
        return None


def fetch(session: requests.Session, lat: float, lon: float,
          d_from: str, d_to: str, use_forecast_api: bool) -> dict[str, dict]:
    """{'YYYY-MM-DDTHH:00': {...}} を返す。"""
    url = FCST if use_forecast_api else HIST
    r = session.get(url, timeout=40, params={
        "latitude": lat, "longitude": lon, "hourly": HOURLY,
        "start_date": d_from, "end_date": d_to, "timezone": "Asia/Tokyo",
    })
    r.raise_for_status()
    h = r.json().get("hourly") or {}
    times = h.get("time") or []
    out = {}
    for i, t in enumerate(times):
        ws = _at(h.get("wind_speed_10m"), i)
        out[t] = {
            "fc_wind_speed": None if ws is None else round(ws * KMH_TO_MS, 3),
            "fc_wind_dir": _at(h.get("wind_direction_10m"), i),
            "fc_precip": _at(h.get("precipitation"), i),
            "fc_weather_code": _at(h.get("weather_code"), i),
        }
    return out


def _at(seq, i):
    try:
        v = seq[i]
    except (TypeError, IndexError):
        return None
    return v


def upsert(rows: list[dict]) -> int:
    if not rows:
        return 0
    with get_connection() as conn:
        # 🔴 実測列（weather / wind_speed / settled_at）を触らないこと。
        #    行ごと REPLACE すると winticket 側の取り込みを消してしまう。
        conn.executemany(
            "INSERT INTO wt_race_conditions "
            "(race_key, fc_weather_code, fc_wind_speed, fc_wind_dir, "
            " fc_precip, fc_source) "
            "VALUES (:race_key, :fc_weather_code, :fc_wind_speed, "
            "        :fc_wind_dir, :fc_precip, :fc_source) "
            "ON CONFLICT (race_key) DO UPDATE SET "
            "  fc_weather_code = EXCLUDED.fc_weather_code, "
            "  fc_wind_speed   = EXCLUDED.fc_wind_speed, "
            "  fc_wind_dir     = EXCLUDED.fc_wind_dir, "
            "  fc_precip       = EXCLUDED.fc_precip, "
            "  fc_source       = EXCLUDED.fc_source", rows)
        conn.commit()
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2022-12-01")
    ap.add_argument("--until", default=None)
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    until = args.until or _date.today().isoformat()
    # アーカイブは概ね前日までしか無い。それ以降は通常の forecast API へ回す。
    archive_max = (_date.today() - timedelta(days=1)).isoformat()

    by_venue = race_hours(args.since, until)
    missing = sorted(set(by_venue) - set(VENUE_COORDS))
    if missing:
        print(f"⚠️ 座標が無い会場（スキップ）: {missing}")

    total = n_fail = 0
    s = requests.Session()
    venues = sorted(set(by_venue) & set(VENUE_COORDS))
    for i, vid in enumerate(venues, 1):
        lat, lon = VENUE_COORDS[vid]
        items = by_venue[vid]
        days = sorted({h[:10] for _rk, h in items})
        rows: list[dict] = []
        # 会場ごとに1年ずつまとめて引く（APIレート配慮）。
        # 🔴 **アーカイブ境界で必ず割る。** 通常の Forecast API は過去日の範囲を
        #    受け付けず 400 を返す（2026-08-18 に "2026-04-22〜2026-08-18" で実際に
        #    踏んだ）。年チャンクごと forecast へ回すと直近年が丸ごと落ちる。
        for chunk in _split_at_archive(_year_chunks(days), archive_max):
            d_from, d_to = chunk[0], chunk[-1]
            use_fcst = d_from > archive_max
            try:
                hourly = fetch(s, lat, lon, d_from, d_to, use_fcst)
            except Exception as e:                      # noqa: BLE001
                n_fail += 1
                print(f"  [{i}/{len(venues)}] {vid} {d_from}〜{d_to}: 失敗 {e}",
                      flush=True)
                time.sleep(args.sleep)
                continue
            src = ("open-meteo-forecast" if use_fcst
                   else "open-meteo-hist-forecast")
            for rk, hour in items:
                if not (d_from <= hour[:10] <= d_to):
                    continue
                v = hourly.get(hour)
                if not v:
                    continue
                rows.append({"race_key": rk, "fc_source": src, **v})
            time.sleep(args.sleep)
        total += len(rows) if args.dry_run else upsert(rows)
        print(f"  [{i}/{len(venues)}] 会場{vid}: {len(rows):,}レース "
              f"（累計 {total:,}）", flush=True)

    print(f"[forecast] {'(dry-run) ' if args.dry_run else ''}"
          f"{total:,}レース / 失敗 {n_fail} チャンク")
    if not args.dry_run:
        with get_connection() as conn:
            for r in conn.execute(
                    # ⚠️ 別名必須（`_PgRow` が同名列を畳む）。
                    "SELECT count(*) AS n_all, count(fc_wind_speed) AS n_ws, "
                    "       count(fc_wind_dir) AS n_wd, "
                    "       count(weather) AS n_w FROM wt_race_conditions"):
                print(f"[forecast] 総数 {r[0]:,} / 予報風速 {r[1]:,} "
                      f"/ 予報風向 {r[2]:,} / 実測天候 {r[3]:,}")


def _split_at_archive(chunks: list[list[str]], archive_max: str) -> list[list[str]]:
    """各チャンクを archive_max の前後で割る。

    前半は historical-forecast（過去のアーカイブ予報）、後半は通常 forecast へ回す。
    割らずに片方の API へまとめて投げると 400 になる。
    """
    out: list[list[str]] = []
    for ch in chunks:
        past = [d for d in ch if d <= archive_max]
        future = [d for d in ch if d > archive_max]
        if past:
            out.append(past)
        if future:
            out.append(future)
    return out


def _year_chunks(days: list[str]) -> list[list[str]]:
    """日付リストを年ごとに束ねる（1リクエストの期間を絞りすぎない）。"""
    by_year: dict[str, list[str]] = defaultdict(list)
    for d in days:
        by_year[d[:4]].append(d)
    return [sorted(v) for _k, v in sorted(by_year.items())]


if __name__ == "__main__":
    main()
