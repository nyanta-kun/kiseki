"""メカウチダの履歴ページから、過去レースの「荒れやすさ」（波乱度）を取り込む。

2026-10-25 ユーザー要望: 一覧・詳細の波乱度を過去レースにも出す。

🔴 **履歴ページの札は確定オッズで作り直したもの**で、利用者が発走前に見た札とは
別物。``keiba.mekauchida_race_upset`` へ ``source='history'`` として入れ、
runner が書いた ``live`` / ``final`` の行は**上書きしない**。今日以降の日付も取り込まない
（今日の札は ``services/mekauchida_runner.py`` が発走前に書く）。

⚠️ 中央の 2026-09-25 以前のページは、サイトが買ったレースにしか札が無い。
札が無いことは「波乱度が無い」ことを意味しない。

既定は dry-run（DB に書かない）。書き込みは ``--apply`` を付けたときだけ::

    # VPS（コンテナ内）
    docker exec galloplab-backend-1 uv run python /app/scripts/mekauchida_upset_backfill.py
    docker exec galloplab-backend-1 uv run python /app/scripts/mekauchida_upset_backfill.py --apply

    # Mac から読み取り専用で dry-run
    ~/bin/kiseki-ro backend/.venv/bin/python backend/scripts/mekauchida_upset_backfill.py \\
        --db-url-env KEIRIN_DB_URL

    # 取得済みの HTML を使う（ファイル名は YYYYMMDD.html）
    ... --cache-dir /path/to/meka   # {cache}/history/ と {cache}/nar/history/ を見る
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import CursorResult, Engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from src.db.session import sync_engine  # noqa: E402
from src.scrapers.mekauchida import (  # noqa: E402
    HISTORY_INDEX_URL,
    HISTORY_URL,
    NAR_HISTORY_INDEX_URL,
    NAR_HISTORY_URL,
    HistoryPage,
    parse_history_index,
    parse_history_page,
)
from src.services.mekauchida_runner import fetch_html  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class PillarConfig:
    """柱ごとの違い（URL・レース表・キャッシュの置き場）。"""

    pillar: str  # keiba.mekauchida_race_upset.pillar
    index_url: str
    page_url: str
    races_table: str
    cache_sub: str


PILLARS = {
    "jra": PillarConfig("jra", HISTORY_INDEX_URL, HISTORY_URL, "keiba.races", "history"),
    "chihou": PillarConfig("chihou", NAR_HISTORY_INDEX_URL, NAR_HISTORY_URL, "chihou.races", "nar/history"),
}

# runner が書いた live / final の行は上書きしない
UPSERT_HISTORY_SQL = text("""
INSERT INTO keiba.mekauchida_race_upset (pillar, race_id, level, site_updated, observed_at, source)
VALUES (:pillar, :race_id, :level, :site_updated, :observed_at, 'history')
ON CONFLICT (pillar, race_id) DO UPDATE
SET level = EXCLUDED.level,
    site_updated = EXCLUDED.site_updated,
    observed_at = EXCLUDED.observed_at
WHERE keiba.mekauchida_race_upset.source = 'history'
""")


def match_rows(page: HistoryPage, db_races: list[tuple[int, str, int]]) -> tuple[list[dict[str, object]], Counter[str]]:
    """履歴ページの札を DB のレースに対応づける。

    Args:
        page: 履歴ページ
        db_races: その日の DB のレース ``(id, course_name, race_number)``

    Returns:
        (書き込む行, 件数の内訳)。内訳のキーは matched / no_chip / unmatched
    """
    ids = {(venue, rno): rid for rid, venue, rno in db_races}
    rows: list[dict[str, object]] = []
    counts: Counter[str] = Counter()
    for race in page.races:
        if race.upset is None:
            counts["no_chip"] += 1
            continue
        rid = ids.get((race.venue, race.race_number))
        if rid is None:
            counts["unmatched"] += 1
            counts[f"unmatched:{race.venue}"] += 1
            continue
        counts["matched"] += 1
        rows.append({"race_id": rid, "level": race.upset, "site_updated": page.updated})
    return rows, counts


def _load_page(cfg: PillarConfig, date: str, cache_dir: Path | None, sleep: float) -> str:
    if cache_dir is not None:
        f = cache_dir / cfg.cache_sub / f"{date}.html"
        if f.exists():
            return f.read_text()
    html = fetch_html(cfg.page_url.format(date=date), retries=2, on_retry=print)
    time.sleep(sleep)
    return html


def run(args: argparse.Namespace) -> int:
    today = datetime.now(JST).strftime("%Y%m%d")
    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    now = datetime.now(JST)
    engine: Engine = create_engine(os.environ[args.db_url_env]) if args.db_url_env else sync_engine
    rc = 0
    for name in args.pillar:
        cfg = PILLARS[name]
        dates = [
            d
            for d in parse_history_index(fetch_html(cfg.index_url, retries=2, on_retry=print))
            if d < today and (args.start is None or d >= args.start) and (args.end is None or d <= args.end)
        ]
        total: Counter[str] = Counter()
        levels: Counter[str] = Counter()
        written = 0
        for date in dates:
            try:
                page = parse_history_page(_load_page(cfg, date, cache_dir, args.sleep), date)
            except Exception as e:  # noqa: BLE001 — 1 日の失敗で全体を止めない
                print(f"[{name}] {date} ERROR: {e}")
                total["page_error"] += 1
                rc = 1
                continue
            with Session(engine) as db:
                db_races = [
                    (r.id, r.course_name, r.race_number)
                    for r in db.execute(
                        text(f"SELECT id, course_name, race_number FROM {cfg.races_table} WHERE date = :d"),
                        {"d": date},
                    )
                ]
                rows, counts = match_rows(page, db_races)
                total.update(counts)
                levels.update(str(r["level"]) for r in rows)
                if args.apply and rows:
                    params = [{**r, "pillar": cfg.pillar, "observed_at": now} for r in rows]
                    for prm in params:
                        res = cast(CursorResult[Any], db.execute(UPSERT_HISTORY_SQL, prm))
                        written += res.rowcount or 0  # live の行に当たったものは 0
                    db.commit()
            if counts.get("unmatched"):
                detail = {k: v for k, v in counts.items() if k.startswith("unmatched:")}
                print(f"[{name}] {date} 対応づかない札 {counts['unmatched']} 件: {detail}")
        print(
            f"[{name}] {len(dates)} 日 / 札あり対応 {total['matched']} 件 "
            f"(低 {levels['低']} / 中 {levels['中']} / 高 {levels['高']}) / "
            f"札なし {total['no_chip']} / 対応づかない {total['unmatched']} / "
            f"ページ失敗 {total['page_error']}"
            + (f" / 書き込み {written} 件" if args.apply else " / dry-run（書き込みなし）")
        )
    return rc


def main() -> int:
    """エントリポイント。"""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--pillar", nargs="+", choices=sorted(PILLARS), default=["jra", "chihou"])
    p.add_argument("--start", help="YYYYMMDD（含む）")
    p.add_argument("--end", help="YYYYMMDD（含む）。今日以降は指定しても取り込まない")
    p.add_argument("--cache-dir", help="取得済み HTML の置き場（無い日はサイトから取る）")
    p.add_argument("--sleep", type=float, default=1.0, help="サイト取得の間隔（秒）")
    p.add_argument(
        "--db-url-env",
        help="接続先 URL を読む環境変数名（ローカルの読み取り専用 dry-run 用。例: KEIRIN_DB_URL）",
    )
    p.add_argument("--apply", action="store_true", help="DB に書き込む（既定は dry-run）")
    return run(p.parse_args())


if __name__ == "__main__":
    sys.exit(main())
