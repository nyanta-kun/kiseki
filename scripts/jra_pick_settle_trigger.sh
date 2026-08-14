#!/bin/bash
# JRA 推奨（hit_tier）の前向き記録に確定結果を書き戻す（日次cron）
#
# VPS cron: 45 23 * * * /home/ysuzuki/GitHub/kiseki/scripts/jra_pick_settle_trigger.sh
#
# ⚠️ VPS の cron は JST で動く。UTC のつもりで書くと 9 時間ずれる。
#
# 冪等。直近 7 日をなめるので、成績の取込が遅れた日も後日拾える
# （未確定のレースは settled_at が NULL のまま残るだけ）。

set -u

BACKEND_URL="http://127.0.0.1:8003"
LOG_FILE="/home/ysuzuki/GitHub/kiseki/logs/jra_pick_settle_trigger.log"
ENV_FILE="/home/ysuzuki/GitHub/kiseki/.env"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a "$LOG_FILE"
}

API_KEY=$(grep '^CHANGE_NOTIFY_API_KEY=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
if [ -z "$API_KEY" ]; then
  log "ERROR: CHANGE_NOTIFY_API_KEY が .env に見つかりません"
  exit 1
fi

for BACK in 0 1 2 3 4 5 6; do
  DATE=$(TZ=Asia/Tokyo date -d "-${BACK} day" '+%Y%m%d' 2>/dev/null \
       || TZ=Asia/Tokyo date -v-${BACK}d '+%Y%m%d')
  RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
    "$BACKEND_URL/api/jra/hit-tier/settle?date=$DATE" \
    -H "X-API-Key: $API_KEY" \
    --max-time 120)
  HTTP_CODE=$(echo "$RESPONSE" | tail -1)
  BODY=$(echo "$RESPONSE" | head -1)
  if [ "$HTTP_CODE" != "200" ]; then
    log "ERROR: settle 失敗 date=$DATE HTTP=$HTTP_CODE body=$(echo "$BODY" | head -c 200)"
    continue
  fi
  RACES=$(echo "$BODY" | python3 -c "import json,sys; print(json.load(sys.stdin).get('races',0))" 2>/dev/null || echo "0")
  [ "$RACES" != "0" ] && log "確定 date=$DATE $BODY"
done
