#!/bin/bash
# 地方競馬オッズ判断トリガー（毎分cron）
# VPS cron: * * * * * /home/ysuzuki/GitHub/kiseki/scripts/chihou_odds_decision_trigger.sh
# 発走8〜15分前レースの buy/pass を更新

BACKEND_URL="http://127.0.0.1:8003"
LOG_FILE="/home/ysuzuki/GitHub/kiseki/logs/chihou_odds_decision_trigger.log"
ENV_FILE="/home/ysuzuki/GitHub/kiseki/.env"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG_FILE"
}

API_KEY=$(grep '^CHANGE_NOTIFY_API_KEY=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
if [ -z "$API_KEY" ]; then
  log "ERROR: CHANGE_NOTIFY_API_KEY が .env に見つかりません"
  exit 1
fi

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
  "$BACKEND_URL/api/chihou/recommendations/update-odds-decision" \
  -H "X-API-Key: $API_KEY" \
  --max-time 30)

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | head -1)

if [ "$HTTP_CODE" = "200" ]; then
  UPDATED=$(echo "$BODY" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('updated',0))" 2>/dev/null || echo "0")
  [ "$UPDATED" != "0" ] && log "オッズ判断更新: ${UPDATED}件"
else
  log "ERROR: オッズ判断失敗 HTTP=$HTTP_CODE"
fi
