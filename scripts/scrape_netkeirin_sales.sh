#!/bin/bash
# netkeirin「ウマい車券」予想家成績・売上（日別）スクレイピング
#
# 【実行場所: VPS（Macではない）】 2026-08-03 にMac LaunchAgent構成から変更した。
# 理由: このツールの認証には netkeirin 入稿ツールと同じ資格情報
# （NETKEIRIN_LOGIN_ID / NETKEIRIN_PASSWORD）が必要で、それは VPS の
# /home/ysuzuki/GitHub/kiseki/keirin/.env にのみ存在する。Mac側へ資格情報を複製するより、
# 既に同じ資格情報で netkeirin_submit_wt.py を動かしている VPS で実行する方が
# 秘密情報の置き場所を増やさずに済む（ユーザー判断・B案）。
#
# 対象ページの注記どおり「通常集計日はレース日の翌日」「売上は速報値」のため、
# 毎日10時台以降（サイト側のバッチ更新後）に前日分を取得してUPSERTする。
#
# 資格情報とDB接続の出どころ:
#   NETKEIRIN_LOGIN_ID / NETKEIRIN_PASSWORD … keirin/.env（唯一の正本）
#   DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD … kiseki/.env（python側が load_dotenv で読む）
# python は keirin の venv を使う（kiseki は VPS 上では Docker 運用で venv が無いため）。

set -u
set -o pipefail   # tee が python の終了コードをマスクしないように

KISEKI_DIR="/home/ysuzuki/GitHub/kiseki"
# 2026-08-10 の kiseki 統合で keirin は ~/keirin から $KISEKI_DIR/keirin へ移動した。
KEIRIN_DIR="$KISEKI_DIR/keirin"
PYTHON="$KEIRIN_DIR/.venv/bin/python"
SCRIPT="$KISEKI_DIR/backend/scripts/scrape_netkeirin_sales.py"
LOG_FILE="$KISEKI_DIR/logs/scrape_netkeirin_sales.log"
LOCK_FILE="/tmp/scrape_netkeirin_sales.lock"

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

# keirin/.env から認証情報だけを export する（他の変数は取り込まない）。
if [ ! -f "$KEIRIN_DIR/.env" ]; then
  log "[FATAL] $KEIRIN_DIR/.env が見つかりません"
  exit 1
fi
NETKEIRIN_LOGIN_ID=$(grep -E '^NETKEIRIN_LOGIN_ID=' "$KEIRIN_DIR/.env" | head -1 | cut -d= -f2-)
NETKEIRIN_PASSWORD=$(grep -E '^NETKEIRIN_PASSWORD=' "$KEIRIN_DIR/.env" | head -1 | cut -d= -f2-)
export NETKEIRIN_LOGIN_ID NETKEIRIN_PASSWORD
if [ -z "$NETKEIRIN_LOGIN_ID" ] || [ -z "$NETKEIRIN_PASSWORD" ]; then
  log "[FATAL] NETKEIRIN_LOGIN_ID / NETKEIRIN_PASSWORD を $KEIRIN_DIR/.env から取得できません"
  exit 1
fi

# DB接続情報を kiseki/.env から export する。
# python 側は python-dotenv があれば .env を直接読むが、VPS で使う keirin の venv には
# 未導入のため、ここで環境変数として渡しておく（どちらの経路でも動くようにする）。
if [ -f "$KISEKI_DIR/.env" ]; then
  for k in DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWORD; do
    v=$(grep -E "^${k}=" "$KISEKI_DIR/.env" | head -1 | cut -d= -f2-)
    [ -n "$v" ] && export "$k=$v"
  done
fi
if [ -z "${DB_HOST:-}" ]; then
  log "[FATAL] DB_HOST を $KISEKI_DIR/.env から取得できません"
  exit 1
fi

log "=== scrape_netkeirin_sales 開始 ==="
cd "$KISEKI_DIR/backend"
# 引数はそのまま python へ渡す（未指定なら「前日分」を自動取得）
"$PYTHON" "$SCRIPT" "$@" 2>&1 | tee -a "$LOG_FILE"
RC=$?
log "=== 終了 (rc=$RC) ==="
exit $RC
