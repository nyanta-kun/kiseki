#!/bin/bash
# 毎日 01:50 実行: netkeirin の全予想家の商品成績（直近3日）を取り込む。
#
# 「予想家比較」タブ（/keirin/stats）の元データ。取り込みの正本は
# src/netkeirin_rivals.py。確定しきったレースは飛ばすので、直近3日を毎回なめても
# 実際に取りに行くのは未確定が残るレースだけ。
#
# 実行時刻: ミッドナイト競輪の最終レース（00:30 頃）と 01:20 の走路条件収集の後、
# 07:00 の daily_picks_wt.sh より前。
set -e
set -o pipefail
cd "$(dirname "$0")/.."

LOG_DIR="data/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/netkeirin_rivals.log"

LOCK_FILE="$LOG_DIR/netkeirin_rivals.lock"
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
  echo "[$(date '+%m-%d %H:%M:%S')] 前回実行がロック中のためスキップ" | tee -a "$LOG"
  exit 0
fi

echo "[$(date '+%m-%d %H:%M:%S')] === 予想家成績の収集 開始 ===" | tee -a "$LOG"
PYTHONPATH=. .venv/bin/python3 scripts/collect_netkeirin_rivals.py --days 3 2>&1 | tee -a "$LOG"
echo "[$(date '+%m-%d %H:%M:%S')] === 予想家成績の収集 完了 ===" | tee -a "$LOG"
