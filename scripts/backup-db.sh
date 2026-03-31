#!/usr/bin/env bash
# PostgreSQL バックアップスクリプト
# 使用方法: ./scripts/backup-db.sh
# cron設定例（毎日3時): 0 3 * * * /path/to/kiseki/scripts/backup-db.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# .env から環境変数を読み込む
if [[ -f "${PROJECT_ROOT}/.env" ]]; then
  set -o allexport
  # shellcheck source=/dev/null
  source "${PROJECT_ROOT}/.env"
  set +o allexport
fi

# DATABASE_URL が未設定の場合はエラー
if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "[ERROR] DATABASE_URL が設定されていません" >&2
  exit 1
fi

BACKUP_DIR="${PROJECT_ROOT}/backups"
mkdir -p "${BACKUP_DIR}"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="${BACKUP_DIR}/keiba_${TIMESTAMP}.sql.gz"

echo "[INFO] バックアップ開始: ${BACKUP_FILE}"
pg_dump "${DATABASE_URL}" --schema=keiba --no-owner --no-privileges \
  | gzip > "${BACKUP_FILE}"

echo "[INFO] バックアップ完了: $(du -sh "${BACKUP_FILE}" | cut -f1)"

# 30日より古いバックアップを削除
find "${BACKUP_DIR}" -name "keiba_*.sql.gz" -mtime +30 -delete
echo "[INFO] 30日以上古いバックアップを削除しました"
