"""netkeiba 走行データの取得ジョブ（中央の平地のみ）。

設計と運用計画: `docs/netkeiba_running_data_plan_2026_09_17.md`。

## 生ページを必ず残す

解析は後から直せるが、取得はやり直しが高い（1件あたり実測 7.7〜8.9 秒・
2024年以降で約9,000件）。そこで**生ページを gzip で保存**し、解析結果は
別ファイルに書く。解析の直しは再取得なしでやり直せる。

## 🔴 契約が無いときは1着馬だけが返る

`RunningData.is_master` が偽なら、そのページは1着馬しか含まない。
**既定では解析結果を書かない**（`allow_gated=True` で明示したときだけ書く）。
書いてしまうと「勝った馬だけ」の偏った標本が貯まり、後から見分けがつかない。

## 公開待ちはスキップであって失敗ではない

走行データはレース後で最初の金曜18時頃に公開される（`running_data.publication_at`）。
それ以前のレースは `not_published` として数え、**取りに行かない**。
"""

from __future__ import annotations

import gzip
import json
import logging
from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import datetime
from pathlib import Path

import requests
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import ip_restriction, running_data
from . import race_id as rid
from .decode import decode_page
from .rate_limiter import RateLimiter
from .session import login

logger = logging.getLogger(__name__)

# 途中で IP 制限を再確認する間隔（レース数）。長時間ジョブなので起動時の1回では足りない
IP_RECHECK_EVERY = 20

# 中央の平地のみ。障害は netkeiba 側が対象外と明記している
_RACES_SQL = text(
    """
    SELECT r.id, r.date, r.course, r.course_name, r.race_number, r.jravan_race_id
    FROM keiba.races r
    WHERE r.date BETWEEN :start AND :end
      AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
      AND r.surface <> '障'
      AND r.jravan_race_id IS NOT NULL
    ORDER BY r.date, r.course, r.race_number
    """
)


@dataclass
class RunningDataResult:
    """1 実行ぶんの結果。

    🔴 スキップ（取得済み・公開待ち）は異常ではない。`fetched` が 0 でも
    失敗とは限らない（[[index_job]] と同じ注意）。
    """

    fetched: int = 0
    parsed_horses: int = 0
    gated: int = 0            # 契約が無く1着馬しか返らなかった
    not_published: int = 0
    skipped: int = 0          # 生ページが既にある
    errors: int = 0
    aborted_reason: str | None = None
    failed_races: list[str] = field(default_factory=list)

    @property
    def handled(self) -> int:
        return self.fetched + self.skipped + self.not_published


def raw_path(out_dir: Path, race_date: date_type, netkeiba_race_id: str) -> Path:
    """生ページの保存先。"""
    return out_dir / race_date.strftime("%Y%m%d") / f"{netkeiba_race_id}.html.gz"


def _write_raw(path: Path, page: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(page)


def _append_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _horse_rows(data: running_data.RunningData, race_id: int, ymd: str,
                netkeiba_race_id: str) -> list[dict]:
    rows = []
    for umaban, h in sorted(data.horses.items()):
        rows.append({
            "race_id": race_id,
            "date": ymd,
            "netkeiba_race_id": netkeiba_race_id,
            "horse_number": umaban,
            "frame_number": h.frame_number,
            "laps": h.laps,
            "furlong_distances": h.furlong_distances,
            "positions": h.positions,
            "running_distance": h.running_distance,
            "finish_time": h.finish_time,
            "corrected_time": h.corrected_time,
            "pitch": h.pitch,
            "stride": h.stride,
        })
    return rows


def scrape(session: Session, *, start: date_type, end: date_type, out_dir: Path,
           user_id: str, password: str, environment_id: str | None = None,
           force: bool = False, allow_gated: bool = False, dry_run: bool = False,
           limit: int | None = None, now: datetime | None = None) -> RunningDataResult:
    """`start`〜`end` の中央平地レースの走行データを取得する。

    Args:
        session: 同期 DB セッション（対象レースの取得にだけ使う）。
        out_dir: 生ページと解析結果の保存先。
        force: 生ページが既にあっても取り直す。
        allow_gated: 🔴 契約が無い（1着馬のみ）ページの解析結果も書く。
        dry_run: ファイルを書かない。
        limit: 取得する最大レース数（お試し用）。
        now: 公開判定に使う時刻（テスト用）。
    """
    result = RunningDataResult()
    try:
        ip_restriction.require_not_restricted(session, context="netkeiba 走行データ")
    except ip_restriction.IPRestricted as e:
        result.aborted_reason = str(e)
        return result

    races = list(session.execute(_RACES_SQL, {"start": start.strftime("%Y%m%d"),
                                              "end": end.strftime("%Y%m%d")}))
    logger.info("対象レース %d 件 (%s〜%s)", len(races), start, end)

    limiter = RateLimiter()
    http: requests.Session | None = None

    for i, race in enumerate(races):
        if limit is not None and result.fetched >= limit:
            logger.info("--limit %d に達したので打ち切る", limit)
            break
        if i and i % IP_RECHECK_EVERY == 0:
            try:
                ip_restriction.require_not_restricted(session, context="netkeiba 走行データ")
            except ip_restriction.IPRestricted as e:
                result.aborted_reason = str(e)
                break

        race_date = datetime.strptime(race.date, "%Y%m%d").date()
        try:
            netkeiba_race_id = rid.jra_race_id(race_date, race.course, race.race_number,
                                               race.jravan_race_id)
        except ValueError as e:
            logger.warning("race_id を作れない: %s", e)
            result.errors += 1
            continue
        label = f"{race.course_name}{race.race_number}R({race_date})"

        if not running_data.is_published(race_date, now):
            result.not_published += 1
            continue
        path = raw_path(out_dir, race_date, netkeiba_race_id)
        if path.exists() and not force:
            result.skipped += 1
            continue

        if http is None:
            http = login(user_id, password, rate_limiter=limiter)
        limiter.wait()
        try:
            res = http.get(running_data_url(netkeiba_race_id), timeout=30)
            res.raise_for_status()
            page = decode_page(res)
        except Exception as e:  # noqa: BLE001 - 取得失敗は次のレースへ進む
            if ip_restriction.looks_like_ip_restriction(str(e)):
                ip_restriction.mark_restricted(
                    session, source="netkeiba 走行データ",
                    environment_id=environment_id or "local")
                result.aborted_reason = f"IP 制限: {e}"
                break
            logger.warning("%s の取得に失敗: %s", label, e)
            result.errors += 1
            result.failed_races.append(label)
            continue

        result.fetched += 1
        if not dry_run:
            _write_raw(path, page)

        data = running_data.parse(page)
        if not data.published:
            # 公開時刻の推定より実際が遅いことがある。取得はしたが中身は空
            logger.info("%s はまだ公開されていない", label)
            continue
        if not data.is_master and not allow_gated:
            result.gated += 1
            if result.gated == 1:
                logger.warning(
                    "🔴 マスターコース契約が無いため1着馬しか返っていない。"
                    "解析結果は書かない（--allow-gated で明示的に書ける）")
            continue
        rows = _horse_rows(data, race.id, race.date, netkeiba_race_id)
        result.parsed_horses += len(rows)
        if not dry_run:
            _append_jsonl(out_dir / race_date.strftime("%Y%m%d") / "parsed.jsonl", rows)
            _append_jsonl(out_dir / race_date.strftime("%Y%m%d") / "races.jsonl", [{
                "race_id": race.id,
                "date": race.date,
                "netkeiba_race_id": netkeiba_race_id,
                "is_master": data.is_master,
                "n_horses": data.n_horses,
                "race_laps": data.race_laps,
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
            }])

    return result


def running_data_url(netkeiba_race_id: str) -> str:
    """走行データページの URL。"""
    return f"{rid.JRA_HOST}/race/ai_laptime.html?race_id={netkeiba_race_id}"
