#!/usr/bin/env bash
# hrdb 自動バックアップ (Mac 側実行・VPS 経由)
#
# VPS sekito から pg_dump -Fc をパイプで Mac に直送し、世代管理する。
# VPS には dump を残さない (容量逼迫対策)。
#
# 使用方法:
#   ./scripts/backup_hrdb.sh                       # 通常実行
#   BACKUP_DIR=/path ./scripts/backup_hrdb.sh      # 保存先変更
#   SSH_HOST=other ./scripts/backup_hrdb.sh        # SSH 接続先変更
#
# launchd / cron から呼ぶ際は絶対パスを指定する。

set -eEuo pipefail

SSH_HOST="${SSH_HOST:-sekito}"
DB_NAME="${DB_NAME:-hrdb}"
BACKUP_DIR="${BACKUP_DIR:-${HOME}/kiseki-backups}"
LOG_FILE="${LOG_FILE:-${BACKUP_DIR}/backup.log}"

DAILY_KEEP=7
WEEKLY_KEEP=4
MONTHLY_KEEP=12

mkdir -p "$BACKUP_DIR"/{daily,weekly,monthly}
mkdir -p "$(dirname "$LOG_FILE")"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

notify_failure() {
  local exit_code=$?
  log "!!! バックアップ失敗 (exit=${exit_code})"
  osascript -e "display notification \"hrdb backup failed (exit=${exit_code}). See ${LOG_FILE}\" with title \"kiseki DB backup\" sound name \"Basso\"" 2>/dev/null || true
  exit "$exit_code"
}
trap notify_failure ERR

DATE=$(date +%Y%m%d)
DOW=$(date +%u)
DOM=$(date +%d)

log "=== バックアップ開始 (${DATE}) ==="

dump_to_file() {
  local label="$1"
  local pg_args="$2"
  local out="$3"
  log "  → ${label}: 開始"
  # shellcheck disable=SC2029
  ssh "$SSH_HOST" "sudo -u postgres pg_dump -Fc -Z 9 ${pg_args} ${DB_NAME}" \
    > "${out}.tmp" 2>>"$LOG_FILE"
  mv "${out}.tmp" "$out"
  local size
  size=$(du -h "$out" | cut -f1)
  log "  → ${label}: 完了 (${size})"
}

# フルダンプ
FULL="$BACKUP_DIR/daily/hrdb-${DATE}.dump"
dump_to_file "フル"  "" "$FULL"

# スキーマ別ダンプ
for SCHEMA in keiba sekito chihou; do
  dump_to_file "${SCHEMA}" "-n ${SCHEMA}" "$BACKUP_DIR/daily/hrdb-${SCHEMA}-${DATE}.dump"
done

# 世代ローテーション (フルダンプのみコピー)
if [[ "$DOW" == "7" ]]; then
  cp "$FULL" "$BACKUP_DIR/weekly/hrdb-${DATE}.dump"
  log "週次世代を作成"
fi
if [[ "$DOM" == "01" ]]; then
  cp "$FULL" "$BACKUP_DIR/monthly/hrdb-${DATE}.dump"
  log "月次世代を作成"
fi

# 古い世代削除
find "$BACKUP_DIR/daily"   -name "hrdb-*.dump" -mtime +$DAILY_KEEP   -delete
find "$BACKUP_DIR/weekly"  -name "hrdb-*.dump" -mtime +$((WEEKLY_KEEP * 7))    -delete
find "$BACKUP_DIR/monthly" -name "hrdb-*.dump" -mtime +$((MONTHLY_KEEP * 31)) -delete

TOTAL=$(du -sh "$BACKUP_DIR" | cut -f1)
log "=== バックアップ完了 (総容量: ${TOTAL}) ==="
