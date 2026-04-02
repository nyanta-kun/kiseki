#!/bin/bash
# PostgreSQL バックアップスクリプト
#
# 使用方法:
#   ./scripts/backup_db.sh
#
# 実行権限の付与（初回のみ）:
#   chmod +x scripts/backup_db.sh
#
# cron設定例（毎日午前3時に実行）:
#   0 3 * * * /path/to/kiseki/scripts/backup_db.sh >> /var/log/kiseki_backup.log 2>&1
#
# 環境変数の設定方法:
#   プロジェクトルートの .env ファイルに以下を設定するか、
#   cron実行時にシェル環境変数として渡すこと:
#
#   DB_HOST=<DBホスト>
#   DB_PORT=5432
#   DB_USER=<DBユーザー>
#   DB_PASSWORD=<DBパスワード>
#   DB_NAME=<DB名>
#
# 注意: .env は絶対にコミットしないこと（.gitignore で除外済み）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# .env から環境変数を読み込む（存在する場合）
if [[ -f "${PROJECT_ROOT}/.env" ]]; then
  set -o allexport
  # shellcheck source=/dev/null
  source "${PROJECT_ROOT}/.env"
  set +o allexport
fi

DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="${BACKUP_DIR:-/var/backups/postgres}"
BACKUP_FILE="$BACKUP_DIR/kiseki_${DATE}.sql.gz"

mkdir -p "$BACKUP_DIR"

# 必須環境変数チェック
if [[ -z "${DB_PASSWORD:-}" ]] || [[ -z "${DB_HOST:-}" ]] || [[ -z "${DB_USER:-}" ]] || [[ -z "${DB_NAME:-}" ]]; then
  echo "[ERROR] 必須環境変数が未設定です: DB_PASSWORD / DB_HOST / DB_USER / DB_NAME" >&2
  echo "[ERROR] .env ファイルを確認してください" >&2
  exit 1
fi

echo "[$(date)] バックアップ開始: $BACKUP_FILE"

PGPASSWORD="${DB_PASSWORD}" pg_dump \
  -h "${DB_HOST}" \
  -p "${DB_PORT:-5432}" \
  -U "${DB_USER}" \
  -d "${DB_NAME}" \
  --schema=keiba \
  --no-owner \
  --no-privileges \
  | gzip > "$BACKUP_FILE"

echo "[$(date)] バックアップ完了: $BACKUP_FILE ($(du -sh "$BACKUP_FILE" | cut -f1))"

# 30日以上古いバックアップを削除
find "$BACKUP_DIR" -name "kiseki_*.sql.gz" -mtime +30 -delete
echo "[$(date)] 30日以上古いバックアップを削除しました"
