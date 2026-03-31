"""2ヶ月単位増分バックテストスクリプト

コンテナ内で直接実行することで、Mac↔コンテナ接続切れの問題を回避する。

使い方（コンテナ内）:
  cd /app
  uv run python3 scripts/backtest_incremental.py --from-end 20241031
  uv run python3 scripts/backtest_incremental.py --from-end 20241031 --oldest 20230101

処理順（デフォルト: 2024-10-31 から遡る）:
  2024-09-01 〜 2024-10-31 → 算出 → バックテスト
  2024-07-01 〜 2024-08-31 → 算出 → バックテスト
  ...
  2023-01-01 〜 2023-02-28 → 算出 → バックテスト（終了）
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("incremental")

_here = Path(__file__).resolve()
_root = _here.parents[1]
REPORT_DIR = _root / "docs" / "verification"
LOG_DIR = Path("/tmp/backtest_incremental")


def prev_month_end(d: date) -> date:
    """指定日の属する月の前月末日を返す。"""
    first_of_month = d.replace(day=1)
    return first_of_month - timedelta(days=1)


def period_start(end: date, months: int = 2) -> date:
    """end 日から months ヶ月前の月初を返す。"""
    m = end.month - months + 1
    y = end.year
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def run_cmd(cmd: list[str], log_path: Path) -> bool:
    """コマンドを実行してログを保存する。成功時 True。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w") as f:
        result = subprocess.run(
            cmd,
            stdout=f,
            stderr=subprocess.STDOUT,
            cwd=str(_root),
        )
    return result.returncode == 0


def calc_indices(start: date, end: date) -> bool:
    """指定期間の v7 総合指数を算出する。"""
    log = LOG_DIR / f"calc_{start:%Y%m%d}_{end:%Y%m%d}.log"
    cmd = [
        sys.executable,
        "scripts/calculate_indices_range.py",
        "--start",
        start.strftime("%Y%m%d"),
        "--end",
        end.strftime("%Y%m%d"),
    ]
    logger.info(f"  指数算出: {start} 〜 {end}")
    ok = run_cmd(cmd, log)
    # 算出完了レース数をカウント
    count = log.read_text(errors="replace").count("算出完了")
    logger.info(f"  → {count} レース算出{'完了' if ok else ' (エラーあり)'}")
    return ok


def run_backtest(start: date, end: date) -> bool:
    """指定期間のバックテストを実行してレポートを保存する。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "scripts/backtest.py",
        "--start",
        start.strftime("%Y%m%d"),
        "--end",
        end.strftime("%Y%m%d"),
        "--report",
        str(REPORT_DIR),
    ]
    log = LOG_DIR / f"backtest_{start:%Y%m%d}_{end:%Y%m%d}.log"
    logger.info(f"  バックテスト: {start} 〜 {end}")
    ok = run_cmd(cmd, log)
    # バックテスト結果サマリーをログから抽出して表示
    text = log.read_text(errors="replace")
    for line in text.splitlines():
        if any(k in line for k in ["単勝的中率", "複勝的中率", "ROI"]):
            print(f"    {line.strip()}")
    return ok


def main() -> None:
    """エントリーポイント。"""
    parser = argparse.ArgumentParser(description="2ヶ月単位増分バックテスト")
    parser.add_argument(
        "--from-end",
        default="20241031",
        help="遡り開始の期間末日 YYYYMMDD (default: 20241031)",
    )
    parser.add_argument(
        "--oldest",
        default="20230101",
        help="遡る最古の開始日 YYYYMMDD (default: 20230101)",
    )
    args = parser.parse_args()

    period_end = date(int(args.from_end[:4]), int(args.from_end[4:6]), int(args.from_end[6:]))
    oldest = date(int(args.oldest[:4]), int(args.oldest[4:6]), int(args.oldest[6:]))

    logger.info("=" * 60)
    logger.info(" 2ヶ月単位増分バックテスト開始")
    logger.info(f" {period_end} から遡って {oldest} まで")
    logger.info("=" * 60)

    completed = []
    failed = []

    while True:
        ps = period_start(period_end, months=2)
        if ps < oldest:
            ps = oldest

        logger.info("")
        logger.info(f"{'─' * 60}")
        logger.info(f" 期間: {ps} 〜 {period_end}")
        logger.info(f"{'─' * 60}")

        # 1. 指数算出
        ok_calc = calc_indices(ps, period_end)
        if not ok_calc:
            logger.error(f"  算出失敗: {ps} 〜 {period_end} をスキップ")
            failed.append((ps, period_end))
        else:
            # 2. バックテスト
            ok_bt = run_backtest(ps, period_end)
            if ok_bt:
                completed.append((ps, period_end))
            else:
                logger.warning("  バックテスト失敗（データ不足の可能性）")
                failed.append((ps, period_end))

        if ps <= oldest:
            break

        # 次の期間へ（今の開始日の前日を末日にする）
        period_end = ps - timedelta(days=1)

    logger.info("")
    logger.info("=" * 60)
    logger.info(f" 処理完了: {len(completed)} 期間成功 / {len(failed)} 期間失敗")
    for s, e in completed:
        logger.info(f"  ✓ {s} 〜 {e}")
    for s, e in failed:
        logger.info(f"  ✗ {s} 〜 {e}")

    reports = sorted(REPORT_DIR.glob("*backtest*.md"))
    logger.info("")
    logger.info(f"レポート ({len(reports)} 件):")
    for r in reports[-10:]:
        logger.info(f"  {r.name}")


if __name__ == "__main__":
    main()
