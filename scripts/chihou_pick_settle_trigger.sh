#!/bin/bash
# 地方競馬 注目馬の前向き記録（確定結果の書き戻し・日次cron）
#
# VPS cron: 30 14 * * * /home/ysuzuki/GitHub/kiseki/scripts/chihou_pick_settle_trigger.sh
#   14:30 UTC = 23:30 JST（全レース確定後）
#
# 直近7日ぶんを毎回なめる。冪等（settled_at が入っている行は対象外）なので
# 二重実行は無害。結果の取り込みが遅れたレースを翌日以降に拾い直すための冗長性。

set -u

BACKEND_URL="http://127.0.0.1:8003"
LOG_FILE="/home/ysuzuki/GitHub/kiseki/logs/chihou_pick_settle_trigger.log"
ENV_FILE="/home/ysuzuki/GitHub/kiseki/.env"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a "$LOG_FILE"
}

API_KEY=$(grep '^CHANGE_NOTIFY_API_KEY=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
if [ -z "$API_KEY" ]; then
  log "ERROR: CHANGE_NOTIFY_API_KEY が .env に見つかりません"
  exit 1
fi

log "=== chihou_pick_settle_trigger.sh 開始 ==="

for i in $(seq 0 6); do
  DATE=$(TZ=Asia/Tokyo date -d "-${i} days" '+%Y%m%d' 2>/dev/null || TZ=Asia/Tokyo date -v-${i}d '+%Y%m%d')
  RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
    "$BACKEND_URL/api/chihou/place-picks/settle?date=$DATE" \
    -H "X-API-Key: $API_KEY" \
    --max-time 120)
  HTTP_CODE=$(echo "$RESPONSE" | tail -1)
  BODY=$(echo "$RESPONSE" | head -1)
  if [ "$HTTP_CODE" = "200" ]; then
    RACES=$(echo "$BODY" | python3 -c "import json,sys; print(json.load(sys.stdin).get('races',0))" 2>/dev/null || echo "?")
    [ "$RACES" != "0" ] && log "確定 date=$DATE: $BODY"
  else
    log "WARN: date=$DATE HTTP=$HTTP_CODE"
  fi
done

log "=== chihou_pick_settle_trigger.sh 完了 ==="
