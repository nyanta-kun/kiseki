#!/bin/bash
# 夏穴 Discord 通知スクリプト
#
# 馬体重発表後(9:00 JST)に夏穴バッジ条件を満たす馬を通知する。
# 夏季(6-9月)以外は natsu_ana_discord_notify.py 側でスキップされる。
#
# VPS cron 設定:
#   0 9 * * * /home/ysuzuki/GitHub/kiseki/scripts/natsu_ana_notify.sh
#   # 9:00 JST（馬体重発表確認後）
#
# 手動実行:
#   /home/ysuzuki/GitHub/kiseki/scripts/natsu_ana_notify.sh
#   /home/ysuzuki/GitHub/kiseki/scripts/natsu_ana_notify.sh --dry-run

set -u

PROJECT_ROOT="/home/ysuzuki/GitHub/kiseki"
LOG_FILE="$PROJECT_ROOT/logs/natsu_ana_notify.log"
CONTAINER="galloplab-backend-1"

mkdir -p "$(dirname "$LOG_FILE")"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a "$LOG_FILE"
}

DRY_RUN="${1:-}"

log "=== natsu_ana_notify.sh 開始 ==="

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  log "ERROR: コンテナが起動していません: $CONTAINER"
  exit 1
fi

if [ -n "$DRY_RUN" ]; then
  log "DRY-RUN モード"
  docker exec "$CONTAINER" uv run python /app/scripts/natsu_ana_discord_notify.py --dry-run >> "$LOG_FILE" 2>&1
else
  docker exec "$CONTAINER" uv run python /app/scripts/natsu_ana_discord_notify.py >> "$LOG_FILE" 2>&1
fi

RC=$?
if [ "$RC" -eq 0 ]; then
  log "完了: rc=$RC"
else
  log "ERROR: rc=$RC"
fi

log "=== natsu_ana_notify.sh 終了 ==="
exit "$RC"
