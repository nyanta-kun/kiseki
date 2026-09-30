#!/bin/bash
# 選手コメント（前検日・レース後）の日次取得（2026-10-01 新設・VPS cron）。
#
#   22:40  post 当日（その日のレース後コメント）   + pre 翌日（翌日が初日の開催の前検日コメント）
#   06:20  post 前日（夜に間に合わなかった分）     + pre 当日（前夜に出ていなかった分）
#
# 引数 night / morning。分類は Mac mini の `tag_rider_interviews.py`（claude -p）が別に回す。
# 失敗しても他の日次処理は止めない（表示のための付随情報）。
set -uo pipefail
cd "$(dirname "$0")/.."
LOG_DIR="data/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/rider_interviews_$(date +%Y-%m-%d).log"

if [[ -z "${KEIRIN_DB_URL:-}" ]]; then
  echo "[$(date '+%H:%M:%S')] [FATAL] KEIRIN_DB_URL が未設定" | tee -a "$LOG" >&2
  exit 1
fi

TODAY=$(date +%Y-%m-%d)
if [[ "$(uname)" == "Darwin" ]]; then
  YESTERDAY=$(date -v-1d +%Y-%m-%d); TOMORROW=$(date -v+1d +%Y-%m-%d)
else
  YESTERDAY=$(date -d yesterday +%Y-%m-%d); TOMORROW=$(date -d tomorrow +%Y-%m-%d)
fi

case "${1:-}" in
  night)   RUNS=("post $TODAY" "pre $TOMORROW") ;;
  morning) RUNS=("post $YESTERDAY" "pre $TODAY") ;;
  *) echo "usage: $0 night|morning" >&2; exit 2 ;;
esac

for r in "${RUNS[@]}"; do
  echo "[$(date '+%H:%M:%S')] collect $r" | tee -a "$LOG"
  # shellcheck disable=SC2086
  .venv/bin/python3 scripts/collect_rider_interviews.py $r 2>&1 | tail -20 | tee -a "$LOG" || \
    echo "[$(date '+%H:%M:%S')] collect $r に失敗（続行）" | tee -a "$LOG"
done
