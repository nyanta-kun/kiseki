#!/bin/bash
# netkeiba 出走想定スクレイピング (Mac LaunchAgent から週次実行)
#
# 水曜夜に実行し、次の週末（土日）全レースの出走想定を取得して
# keiba.projected_entries + keiba.races(placeholder) に保存する。
# netkeiba の出走想定は水曜 20:00 までに出揃うため、その後に走らせる。
# 確定出馬表(JV-Link RA/SE)が木曜に入るまでの「想定」を kiseki 一覧/詳細に表示する。

set -u

PROJECT_ROOT="/Users/ysuzuki/GitHub/kiseki"
BACKEND_DIR="$PROJECT_ROOT/backend"
PYTHON="$BACKEND_DIR/.venv/bin/python"
SCRIPT="$BACKEND_DIR/scripts/scrape_projected_entries.py"
LOG_FILE="$PROJECT_ROOT/logs/scrape_projected_entries.log"
LOCK_FILE="/tmp/scrape_projected_entries.lock"

mkdir -p "$(dirname "$LOG_FILE")"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a "$LOG_FILE"
}

if [ -e "$LOCK_FILE" ]; then
  PID=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    log "別プロセス (PID=$PID) が動作中 - スキップ"
    exit 0
  else
    log "stale ロック削除"
    rm -f "$LOCK_FILE"
  fi
fi

echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT INT TERM

log "=== scrape_projected_entries 開始 ==="
cd "$BACKEND_DIR"
# --dates 省略で「次の土日」を自動算出
"$PYTHON" "$SCRIPT" 2>&1 | tee -a "$LOG_FILE"
RC=$?
log "=== 終了 (rc=$RC) ==="
exit $RC
