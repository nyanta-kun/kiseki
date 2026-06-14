"""
protocol_dm_orchestrator.py — Protocol版 DM指数取得オーケストレーター (Mac側)

JV-Next の独自プロトコルを使って 1403 を直接取得するオーケストレーター。
従来の dm_fetch_orchestrator.py (Denma経由) と違い、K=0 primary venue
(福島・新潟・中京・小倉) も含む全場の過去レースを完全自動取得可能。

仕組み:
  1. DB から未取得レースの (date, course) リストを取得
  2. Windows VM に protocol_dm_pipeline.py をデプロイ・起動
  3. パイプラインが KEY 取得 → 全レース取得 → 永続ストア保存 → DB import
  4. 完了を待って結果を表示

使い方:
  # DB駆動: 未取得レースを自動検出して取得 (デフォルト: 過去30日 + 14日先)
  python3 scripts/protocol_dm_orchestrator.py --from-db

  # 場コード絞り込み (K=0 primary 集中取得)
  python3 scripts/protocol_dm_orchestrator.py --from-db --courses 03,04,07,10

  # 過去全期間バックフィル
  python3 scripts/protocol_dm_orchestrator.py --from-db --since 20230101 --courses 03,04,07,10

  # 日付指定
  python3 scripts/protocol_dm_orchestrator.py --dates 20260419,20260412

  # DRY RUN
  python3 scripts/protocol_dm_orchestrator.py --from-db --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
load_dotenv(Path(__file__).resolve().parents[2] / ".env")


WINDOWS_PIPELINE = "protocol_dm_pipeline.py"
WINDOWS_DIR = "C:/kiseki/windows-agent"
LOG_FILE_REMOTE = "C:/kiseki/data/protocol_pipeline.log"
SESSION_FILE_REMOTE = "C:/kiseki/data/protocol_session.json"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# DB問い合わせ
# ---------------------------------------------------------------------------

def query_missing_dates(courses: list[str], since: str | None,
                         until: str | None = None) -> list[dict]:
    """DBから未取得 (date, course) リストを取得.
    Returns: [{"date": "20260419", "courses": ["03", "05"]}, ...]
    """
    from sqlalchemy import create_engine, text

    from config import settings

    db_url = settings.database_url_sync
    if not db_url:
        raise RuntimeError("database_url_sync not configured")

    engine = create_engine(db_url)
    since_date = since or (date.today() - timedelta(days=30)).strftime("%Y%m%d")
    until_date = until or (date.today() + timedelta(days=14)).strftime("%Y%m%d")

    course_filter = ""
    if courses:
        quoted = ", ".join(f"'{c}'" for c in courses)
        course_filter = f"AND r.course IN ({quoted})"

    # 全頭NULLのレースが存在する (date, course) のみ対象にする。
    # 取消・除外馬による永続的な1-2頭NULLをmissingと誤検出しないため。
    sql = text(f"""
        SELECT DISTINCT r.date, r.course
        FROM keiba.races r
        WHERE r.date >= :since
          AND r.date <= :until
          {course_filter}
          AND r.id IN (
            SELECT race_id
            FROM keiba.race_entries
            GROUP BY race_id
            HAVING COUNT(*) = COUNT(CASE WHEN jvan_time_dm IS NULL THEN 1 END)
          )
        ORDER BY r.date, r.course
    """)

    with engine.connect() as conn:
        rows = conn.execute(sql, {"since": since_date, "until": until_date}).fetchall()

    by_date: dict[str, list[str]] = {}
    for r in rows:
        d = str(r.date)
        cc = str(r.course).zfill(2)
        by_date.setdefault(d, []).append(cc)

    return [{"date": d, "courses": sorted(ccs)} for d, ccs in sorted(by_date.items())]


# ---------------------------------------------------------------------------
# Windows 連携
# ---------------------------------------------------------------------------

def deploy_pipeline_script() -> bool:
    """protocol_dm_pipeline.py を Windows にデプロイ."""
    src = Path(__file__).resolve().parents[2] / "windows-agent" / WINDOWS_PIPELINE
    if not src.exists():
        log(f"ERROR: {src} not found")
        return False
    r = subprocess.run(
        ["scp", str(src), f"windows-vm:{WINDOWS_DIR}/{WINDOWS_PIPELINE}"],
        capture_output=True, timeout=30,
    )
    if r.returncode == 0:
        log(f"deployed {WINDOWS_PIPELINE}")
    else:
        log(f"WARN: scp failed: {r.stderr.decode(errors='replace')[:100]}")
    return r.returncode == 0


def write_session_remote(targets: list[dict]) -> bool:
    """対象 (date, courses) を Windows 側 session.json に書き込み."""
    # session 形式は protocol_dm_pipeline.py が読む形式に合わせる
    # 全 dates を1つの courses に集約し、後で各日ごとに走らせる方が良い
    # → ここでは pipeline に「個別フェッチ」させるため、すべての date×courses を分解
    by_date = {t["date"]: t["courses"] for t in targets}
    # 日付ごとの場リストを統合: pipeline は --dates --courses で複数渡せるが
    # 場が日付毎に異なるので、各日付別に呼ぶのが安全
    return by_date


def run_pipeline_remote(by_date: dict[str, list[str]], no_import: bool = False,
                        force_refresh_key: bool = False, timeout: int = 1800,
                        batch_per_venue: bool = True) -> dict:
    """Windows 上で protocol_dm_pipeline.py を呼び出し.

    batch_per_venue=True (デフォルト): 全 (date,course) を場ごとにグルーピングして
      場ごとに pipeline を1回呼ぶ. KEY refresh が場ごとに1回のみで効率的.

    False: 旧来の 1日付ごとに呼ぶ方式 (KEY refresh が日数分発生).
    """
    results = {"per_call": [], "total_saved": 0, "total_skipped": 0, "total_failed": 0}

    if batch_per_venue:
        # 場ごとに groupby
        by_venue: dict[str, list[str]] = {}
        for date_str, ccs in by_date.items():
            for cc in ccs:
                by_venue.setdefault(cc, []).append(date_str)

        for cc, dates in sorted(by_venue.items()):
            dates = sorted(set(dates))
            log(f"  Pipeline for CC={cc}: {len(dates)} dates")
            flags = ["--dates", ",".join(dates), "--courses", cc, "--no-import"]
            if force_refresh_key:
                flags.append("--force-refresh-key")
                force_refresh_key = False  # 初回のみ
            cmd = ["ssh", "windows-vm",
                   f"C:\\Python312-32\\python.exe {WINDOWS_DIR.replace('/', '\\\\')}\\\\{WINDOWS_PIPELINE} " +
                   " ".join(flags)]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                log(f"  TIMEOUT for CC={cc}")
                results["per_call"].append({"cc": cc, "error": "timeout"})
                continue
            saved = skipped = failed = 0
            for line in r.stdout.splitlines():
                if "fetch summary:" in line:
                    parts = line.split()
                    for p in parts:
                        if p.startswith("saved="): saved = int(p.split("=")[1])
                        elif p.startswith("skipped="): skipped = int(p.split("=")[1])
                        elif p.startswith("failed="): failed = int(p.split("=")[1])
                    break
            results["per_call"].append({"cc": cc, "saved": saved, "skipped": skipped,
                                         "failed": failed, "rc": r.returncode})
            results["total_saved"] += saved
            results["total_skipped"] += skipped
            results["total_failed"] += failed
            log(f"    CC={cc}: saved={saved} skipped={skipped} failed={failed}")

        # 全体 import を1回だけ実行
        if not no_import and results["total_saved"] > 0:
            log("  Running importer (all)")
            cmd = ["ssh", "windows-vm",
                   "C:\\Python312-32\\python.exe C:\\\\kiseki\\\\windows-agent\\\\jvnext_dm_importer.py --all"]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout * 2)
                # 完了行を抽出
                for line in r.stdout.splitlines()[-5:]:
                    log(f"    {line.strip()}")
            except subprocess.TimeoutExpired:
                log("  importer TIMEOUT")
        return results

    # 旧方式 (per-date)
    for date_str, ccs in by_date.items():
        ccs_arg = ",".join(ccs)
        flags = ["--date", date_str, "--courses", ccs_arg]
        if no_import:
            flags.append("--no-import")
        if force_refresh_key:
            flags.append("--force-refresh-key")
            force_refresh_key = False

        log(f"  Running pipeline for {date_str} CC={ccs_arg}")
        cmd = ["ssh", "windows-vm",
               f"C:\\Python312-32\\python.exe {WINDOWS_DIR.replace('/', '\\\\')}\\\\{WINDOWS_PIPELINE} " +
               " ".join(flags)]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            log(f"  TIMEOUT for {date_str}")
            continue
        saved = skipped = failed = 0
        for line in r.stdout.splitlines():
            if "fetch summary:" in line:
                parts = line.split()
                for p in parts:
                    if p.startswith("saved="): saved = int(p.split("=")[1])
                    elif p.startswith("skipped="): skipped = int(p.split("=")[1])
                    elif p.startswith("failed="): failed = int(p.split("=")[1])
                break
        results["per_call"].append({"date": date_str, "saved": saved, "skipped": skipped,
                                     "failed": failed})
        results["total_saved"] += saved
        results["total_skipped"] += skipped
        results["total_failed"] += failed
        log(f"    {date_str}: saved={saved} skipped={skipped} failed={failed}")

    return results


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Protocol版 DM指数取得オーケストレーター")
    parser.add_argument("--from-db", action="store_true",
                        help="DB から未取得レースを自動検出")
    parser.add_argument("--dates", help="カンマ区切り日付 (例: 20260419,20260420)")
    parser.add_argument("--courses", help="カンマ区切り場コード (例: 03,04,07,10)")
    parser.add_argument("--since", help="検索開始日 YYYYMMDD (--from-db 時)")
    parser.add_argument("--until", help="検索終了日 YYYYMMDD (--from-db 時)")
    parser.add_argument("--no-import", action="store_true",
                        help="DB importer を実行しない")
    parser.add_argument("--force-refresh-key", action="store_true",
                        help="初回 KEY 取得を強制 refresh (キャッシュ無視)")
    parser.add_argument("--dry-run", action="store_true",
                        help="取得対象を確認のみ")
    parser.add_argument("--timeout", type=int, default=1800,
                        help="各日のタイムアウト秒 (デフォルト 1800)")
    args = parser.parse_args()

    # 取得対象決定
    if args.from_db:
        courses_filter = args.courses.split(",") if args.courses else []
        log(f"Querying DB for missing dates (since={args.since or 'last 30 days'}, "
            f"courses={courses_filter or 'all'})...")
        targets = query_missing_dates(courses_filter, args.since, args.until)
        if not targets:
            log("No missing dates found in DB.")
            return 0
        log(f"Found {len(targets)} dates with missing DM data")
        for t in targets[:10]:
            log(f"  {t['date']} courses={t['courses']}")
        if len(targets) > 10:
            log(f"  ... ({len(targets) - 10} more)")
    elif args.dates:
        dates = args.dates.split(",")
        ccs = args.courses.split(",") if args.courses else \
              ["03", "04", "05", "06", "07", "08", "09", "10"]
        targets = [{"date": d, "courses": ccs} for d in dates]
    else:
        parser.print_help()
        return 2

    if args.dry_run:
        log("[DRY RUN] would fetch:")
        total = 0
        for t in targets:
            n = len(t["courses"]) * 12
            total += n
            log(f"  {t['date']} courses={t['courses']} ({n} races)")
        log(f"Total: {total} races")
        return 0

    # script デプロイ
    if not deploy_pipeline_script():
        return 1

    # 実行
    by_date = {t["date"]: t["courses"] for t in targets}
    results = run_pipeline_remote(
        by_date,
        no_import=args.no_import,
        force_refresh_key=args.force_refresh_key,
        timeout=args.timeout,
    )

    log("=" * 60)
    log(f"OVERALL: saved={results['total_saved']} "
        f"skipped={results['total_skipped']} failed={results['total_failed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
