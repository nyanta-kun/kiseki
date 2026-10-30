"""POG 週報「赤兎週報」を作って Discord へ PDF 添付で送る。

毎週水曜 20:30（Mac mini の launchd・`scripts/notify_pog_report.sh`）。
水曜 19:00 に netkeiba の出走想定が `keiba.projected_entries` へ入り、
19:30 に出走想定の通知（`notify_pog_entries.py --mode entry`）が出た後に送る。

    python scripts/notify_pog_report.py                     # 作って送る
    python scripts/notify_pog_report.py --dry-run --out /tmp/pog.pdf   # 送らずに PDF だけ
    python scripts/notify_pog_report.py --dry-run --no-netkeiba --out /tmp/pog.pdf

同じ日に二度は送らない（`keiba.pog_notifications` の `notification_type='report'`）。
送り直すときは `--force`。

終了コード: 0 = 正常（送るグループが無いのも正常）。1 = 送信か PDF 化に失敗した。
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from contextlib import nullcontext
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from sqlalchemy.orm import Session  # noqa: E402

from src.scrapers.netkeiba.ip_restriction import IPRestricted  # noqa: E402
from src.scrapers.netkeiba.rate_limiter import RateLimiter  # noqa: E402
from src.services import pog_report_io as io  # noqa: E402
from src.services.pog_report import (  # noqa: E402
    WORKOUT_DAYS,
    Report,
    build_discord_content,
    build_html,
    merge_entries,
    parse_status,
    pick_workouts,
    recent_posts,
    reconcile_status,
    summarize_bbs,
)
from src.utils.cron_run import RunRecord, record  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")
logger = logging.getLogger("notify_pog_report")


@dataclass
class Options:
    """実行オプション。"""

    dry_run: bool = False
    out: Path | None = None
    netkeiba: bool = True
    force: bool = False
    bbs_days: int = 7
    limit_horses: int | None = None


def build_report(session: Session, group: dict, opts: Options, now: datetime) -> Report:
    """1 グループぶんの紙面を組み立てる（送信はしない）。"""
    gid = group["id"]
    today = now.date()
    horses = io.load_horses(session, gid)
    standings = io.load_standings(session, gid)
    confirmed, last_week = io.load_recent(session, gid)
    projected = io.load_projected(session, gid, today, today + timedelta(days=6))
    this_week = attach_workouts(session, merge_entries(confirmed, projected))
    entered = {e.horse_id for e in this_week}

    last_runs = io.load_last_runs(session, [h.horse_id for h in horses])
    for h in horses:
        h.last, h.last_run = last_runs.get(h.horse_id, ("", None))
        h.entered = h.horse_id in entered
    logger.info(
        "%s(%d年): 指名馬 %d頭 / 馬主 %d人 / 今週 %d件 / 先週 %d件",
        group["name"],
        group["year"],
        len(horses),
        len(standings),
        len(this_week),
        len(last_week),
    )

    if opts.netkeiba:
        fetch_netkeiba(session, horses, opts, now)

    name = group["name"] or "POG"
    return Report(
        title=f"{name}週報",
        subtitle=f"{group['year']}年度 {name}POG",
        issued_at=now,
        horses=horses,
        this_week=this_week,
        last_week=last_week,
        standings=standings,
        bbs_days=opts.bbs_days,
    )


def attach_workouts(session: Session, entries: list) -> list:
    """今週の出走予定に、レース前の追い切りを付ける。"""
    if not entries:
        return entries
    start = min(e.day for e in entries) - timedelta(days=WORKOUT_DAYS)
    end = max(e.day for e in entries)
    ws = io.load_workouts(session, sorted({e.horse_id for e in entries}), start, end)
    return [replace(e, workouts=pick_workouts(ws.get(e.horse_id, []), e.day)) for e in entries]


def fetch_netkeiba(session: Session, horses: list, opts: Options, now: datetime) -> None:
    """近況と掲示板を馬ごとに取る。IP 制限なら残りを諦めて紙面は出す。"""
    fetcher = io.NetkeibaFetcher(session=session, limiter=RateLimiter(), http=io.make_http())
    targets = horses[: opts.limit_horses] if opts.limit_horses else horses
    naive_now = now.replace(tzinfo=None)
    for i, h in enumerate(targets, 1):
        try:
            h.status = reconcile_status(parse_status(fetcher.status_text(h.horse_id)), h.last_run)
            h.bbs = summarize_bbs(recent_posts(fetcher.bbs(h.horse_id), naive_now, opts.bbs_days))
        except IPRestricted as e:
            logger.warning("%s — 残り %d頭の netkeiba 情報は載せません", e, len(targets) - i + 1)
            return
        except Exception as e:  # noqa: BLE001 — 1 頭の失敗で号を落とさない
            logger.warning("netkeiba 取得失敗 %s %s: %s", h.horse_id, h.name, e)
        if i % 10 == 0:
            logger.info("  netkeiba %d/%d", i, len(targets))


def run_group(session: Session, group: dict, opts: Options, now: datetime) -> str:
    """1 グループを処理する。戻り値は sent / already / dry / failed。"""
    today = now.date()
    if not opts.force and not opts.dry_run and io.already_sent(session, group["id"], today):
        logger.info("%s(%d年): 本日分は送信済み", group["name"], group["year"])
        return "already"

    rep = build_report(session, group, opts, now)
    filename = f"POG_{today:%Y%m%d}.pdf"
    with tempfile.TemporaryDirectory(prefix="pog_report_") as tmp:
        pdf = io.render_pdf(build_html(rep), opts.out or Path(tmp) / filename)
        logger.info("PDF: %s (%s bytes)", pdf, f"{pdf.stat().st_size:,}")
        content = build_discord_content(rep)
        if opts.dry_run:
            logger.info("[dry-run] 送らない。本文:\n%s", content)
            return "dry"
        if not io.send_discord_file(group["discord_webhook_url"], content, pdf, filename):
            return "failed"
    io.record_sent(session, group["id"], today, f"週報 今週{len(rep.this_week)}件・先週{len(rep.last_week)}件")
    return "sent"


def main() -> int:
    parser = argparse.ArgumentParser(description="POG 週報を PDF にして Discord へ送る")
    parser.add_argument("--dry-run", action="store_true", help="送らない（記録もしない）")
    parser.add_argument("--out", type=Path, help="PDF の保存先（省略時は送信後に消す）")
    parser.add_argument("--no-netkeiba", action="store_true", help="近況・掲示板を取りに行かない")
    parser.add_argument("--force", action="store_true", help="送信済みでも送る")
    parser.add_argument("--bbs-days", type=int, default=7, help="掲示板を何日さかのぼるか")
    parser.add_argument("--limit-horses", type=int, help="netkeiba を見る頭数（試験用）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
    opts = Options(
        dry_run=args.dry_run,
        out=args.out,
        netkeiba=not args.no_netkeiba,
        force=args.force,
        bbs_days=args.bbs_days,
        limit_horses=args.limit_horses,
    )

    from src.db.session import SyncSessionLocal

    ctx = nullcontext(RunRecord(job_name="(記録しない)")) if args.dry_run else record("notify_pog_report")
    counts: dict[str, int] = {}
    with ctx as run, SyncSessionLocal() as session:
        now = datetime.now(JST)
        for group in io.load_groups(session):
            settings = group["notification_settings"] or {}
            if settings.get("report_notification") is False:
                continue
            try:
                status = run_group(session, group, opts, now)
            except Exception:
                logger.exception("%s(%d年): 週報を作れませんでした", group["name"], group["year"])
                status = "failed"
            counts[status] = counts.get(status, 0) + 1
        run.summary = " ".join(f"{k}{v}" for k, v in sorted(counts.items())) or "対象グループなし"

    logger.info("=== 週報: %s ===", counts or "対象グループなし")
    return 1 if counts.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
