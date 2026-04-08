#!/bin/bash
# 地方競馬推奨結果反映トリガー
# VPS cron: 0 13 * * * /home/ysuzuki/GitHub/kiseki/scripts/chihou_results_trigger.sh
# 13:00 UTC = 22:00 JST（レース終了後）

BACKEND_URL="http://127.0.0.1:8003"
LOG_FILE="/home/ysuzuki/GitHub/kiseki/logs/chihou_results_trigger.log"
ENV_FILE="/home/ysuzuki/GitHub/kiseki/.env"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a "$LOG_FILE"
}

API_KEY=$(grep '^CHANGE_NOTIFY_API_KEY=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
if [ -z "$API_KEY" ]; then
  log "ERROR: CHANGE_NOTIFY_API_KEY が .env に見つかりません"
  exit 1
fi

log "=== chihou_results_trigger.sh 開始 ==="

# 直近7日分の結果を反映
for i in $(seq 0 6); do
  DATE=$(TZ=Asia/Tokyo date -d "-${i} days" '+%Y%m%d' 2>/dev/null || TZ=Asia/Tokyo date -v-${i}d '+%Y%m%d')
  RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
    "$BACKEND_URL/api/chihou/recommendations/update-results?date=$DATE" \
    -H "X-API-Key: $API_KEY" \
    --max-time 30)
  HTTP_CODE=$(echo "$RESPONSE" | tail -1)
  BODY=$(echo "$RESPONSE" | head -1)
  if [ "$HTTP_CODE" = "200" ]; then
    UPDATED=$(echo "$BODY" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('updated',0))" 2>/dev/null || echo "?")
    log "結果反映 date=$DATE: ${UPDATED}件"
  else
    log "WARN: date=$DATE HTTP=$HTTP_CODE"
  fi
done

log "=== chihou_results_trigger.sh 完了 ==="
