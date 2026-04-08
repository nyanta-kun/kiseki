#!/bin/bash
# 地方競馬推奨生成トリガー
# VPS cron: 0 1 * * * /home/ysuzuki/GitHub/kiseki/scripts/chihou_recommend_trigger.sh
# 1:00 UTC = 10:00 JST

BACKEND_URL="http://127.0.0.1:8003"
LOG_FILE="/home/ysuzuki/GitHub/kiseki/logs/chihou_recommend_trigger.log"
ENV_FILE="/home/ysuzuki/GitHub/kiseki/.env"
DATE=$(TZ=Asia/Tokyo date '+%Y%m%d')

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a "$LOG_FILE"
}

# .env から CHANGE_NOTIFY_API_KEY を読み込む
API_KEY=$(grep '^CHANGE_NOTIFY_API_KEY=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
if [ -z "$API_KEY" ]; then
  log "ERROR: CHANGE_NOTIFY_API_KEY が .env に見つかりません"
  exit 1
fi

log "=== chihou_recommend_trigger.sh 開始 date=$DATE ==="

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
  "$BACKEND_URL/api/chihou/recommendations/generate?date=$DATE" \
  -H "X-API-Key: $API_KEY" \
  --max-time 120)

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | head -1)

if [ "$HTTP_CODE" = "200" ]; then
  COUNT=$(echo "$BODY" | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d))" 2>/dev/null || echo "?")
  log "推奨生成完了: ${COUNT}件"
else
  log "ERROR: 推奨生成失敗 HTTP=$HTTP_CODE"
  exit 1
fi

log "=== chihou_recommend_trigger.sh 完了 ==="
