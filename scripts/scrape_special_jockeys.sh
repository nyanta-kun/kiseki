#!/bin/bash
# 特別登録馬の想定騎手スクレイピング (Mac LaunchAgent から定期実行)
#
# JV-Link TOKU 取得後 (Windows 18:00 daily) の 30 分後に走らせる。
# expected_jockey_name IS NULL の特別登録レースに対し、
# netkeiba shutuba.html から想定騎手を取得して DB に補完する。

set -u

PROJECT_ROOT="/Users/ysuzuki/GitHub/kiseki"
BACKEND_DIR="$PROJECT_ROOT/backend"
PYTHON="$BACKEND_DIR/.venv/bin/python"
SCRIPT="$BACKEND_DIR/scripts/scrape_special_jockeys.py"
LOG_FILE="$PROJECT_ROOT/logs/scrape_special_jockeys.log"
LOCK_FILE="/tmp/scrape_special_jockeys.lock"

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

log "=== scrape_special_jockeys 開始 ==="
cd "$BACKEND_DIR"
"$PYTHON" "$SCRIPT" 2>&1 | tee -a "$LOG_FILE"
RC=$?
log "=== 終了 (rc=$RC) ==="
exit $RC
