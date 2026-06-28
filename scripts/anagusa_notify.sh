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
LOG_FILE="$PROJECT_ROOT/logs/anagusa_notify.log"
CONTAINER="galloplab-backend-1"

mkdir -p "$(dirname "$LOG_FILE")"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a "$LOG_FILE"
}

DRY_RUN="${1:-}"

log "=== anagusa_notify.sh 開始 ==="

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  log "ERROR: コンテナが起動していません: $CONTAINER"
  exit 1
fi

if [ -n "$DRY_RUN" ]; then
  log "DRY-RUN モード"
  docker exec "$CONTAINER" uv run python /app/scripts/anagusa_discord_notify.py --dry-run >> "$LOG_FILE" 2>&1
else
  docker exec "$CONTAINER" uv run python /app/scripts/anagusa_discord_notify.py >> "$LOG_FILE" 2>&1
fi

RC=$?
if [ "$RC" -eq 0 ]; then
  log "完了: rc=$RC"
else
  log "ERROR: rc=$RC"
fi

log "=== anagusa_notify.sh 終了 ==="
exit "$RC"
