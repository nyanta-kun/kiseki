#!/bin/bash
# 前日発売オッズ取得を Windows Agent へトリガー
#
# VPS cron 設定例（前日から翌日のレースオッズを1時間ごとに取得）:
#   0 0-14 * * * /home/ysuzuki/GitHub/kiseki/scripts/odds_prefetch_trigger.sh
# JST換算: UTC 0:00-14:00 = JST 9:00-23:00 （JRA前日発売開始〜終了目安）
#
# 使い方:
#   ./odds_prefetch_trigger.sh              # 翌日のオッズを取得
#   ./odds_prefetch_trigger.sh 20260406     # 指定日のオッズを取得

BACKEND_URL="http://127.0.0.1:8003"  # VPS backend (galloplab-backend-1コンテナ)
LOG_FILE="/home/ysuzuki/GitHub/kiseki/logs/odds_prefetch_trigger.log"
ENV_FILE="/home/ysuzuki/GitHub/kiseki/.env"
FETCH_DATE="${1:-}"  # 引数あれば使用、なければ翌日（agent側でデフォルト）

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a "$LOG_FILE"
}

log "=== odds_prefetch_trigger.sh 開始 ${FETCH_DATE:+(date=$FETCH_DATE)} ==="

API_KEY=$(grep '^CHANGE_NOTIFY_API_KEY=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
if [ -z "$API_KEY" ]; then
  log "ERROR: CHANGE_NOTIFY_API_KEY が .env に見つかりません"
  exit 1
fi

# パラメータ組み立て
if [ -n "$FETCH_DATE" ]; then
  PARAMS="{\"date\": \"$FETCH_DATE\"}"
else
  PARAMS="{}"
fi

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$BACKEND_URL/api/agent/command" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d "{\"action\": \"odds_prefetch\", \"params\": $PARAMS}" \
  --max-time 10)

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | head -1)

if [ "$HTTP_CODE" = "200" ]; then
  log "odds_prefetch コマンドをキュー投入: $BODY"
else
  log "ERROR: コマンド投入失敗 HTTP=$HTTP_CODE body=$BODY"
  exit 1
fi

log "=== odds_prefetch_trigger.sh 完了 ==="
