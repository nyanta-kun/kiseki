#!/bin/bash
# 穴ぐさ × 指数上位 Discord 通知スクリプト
#
# 毎朝 sekito 穴ぐさスクレイプ後に実行し、
# 穴ぐさA/B かつ v26指数2位以内の馬がいれば Discord に通知する。
#
# VPS cron 設定:
#   30 23 * * * /home/ysuzuki/GitHub/kiseki/scripts/anagusa_notify.sh
#   # 23:30 UTC = 08:30 JST
#
# 手動実行:
#   /home/ysuzuki/GitHub/kiseki/scripts/anagusa_notify.sh
#   /home/ysuzuki/GitHub/kiseki/scripts/anagusa_notify.sh --dry-run

set -u

PROJECT_ROOT="/home/ysuzuki/GitHub/kiseki"
BACKEND_DIR="$PROJECT_ROOT/backend"
PYTHON="$BACKEND_DIR/.venv/bin/python"
SCRIPT="$BACKEND_DIR/scripts/anagusa_discord_notify.py"
LOG_FILE="$PROJECT_ROOT/logs/anagusa_notify.log"

mkdir -p "$(dirname "$LOG_FILE")"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a "$LOG_FILE"
}

DRY_RUN="${1:-}"

log "=== anagusa_notify.sh 開始 ==="

if [ ! -f "$PYTHON" ]; then
  log "ERROR: Python venv not found: $PYTHON"
  exit 1
fi

if [ -n "$DRY_RUN" ]; then
  log "DRY-RUN モード"
  "$PYTHON" "$SCRIPT" --dry-run >> "$LOG_FILE" 2>&1
else
  "$PYTHON" "$SCRIPT" >> "$LOG_FILE" 2>&1
fi

RC=$?
if [ "$RC" -eq 0 ]; then
  log "完了: rc=$RC"
else
  log "ERROR: rc=$RC"
fi

log "=== anagusa_notify.sh 終了 ==="
exit "$RC"
