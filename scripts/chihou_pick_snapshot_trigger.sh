#!/bin/bash
# 地方競馬 注目馬の前向き記録（発走直前スナップショット・毎分cron）
#
# VPS cron: * * * * * /home/ysuzuki/GitHub/kiseki/scripts/chihou_pick_snapshot_trigger.sh
#
# 発走 6 分前以内のレースを1回だけ記録する。撮り逃したレースは記録に残らない
# （発走後に撮ると締切間際の資金移動が混ざり look-ahead になるため、意図的にそうしている）。
#
# 🔴 **毎分で回すこと。** 5分間隔にすると発走6分前の窓を跨げないレースが出る。
#    対象が無い分は DB を触らず 0 件で戻るので負荷は無視できる。

set -u

BACKEND_URL="http://127.0.0.1:8003"
LOG_FILE="/home/ysuzuki/GitHub/kiseki/logs/chihou_pick_snapshot_trigger.log"
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
  "$BACKEND_URL/api/chihou/place-picks/snapshot" \
  -H "X-API-Key: $API_KEY" \
  --max-time 60)

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | head -1)

if [ "$HTTP_CODE" = "200" ]; then
  RACES=$(echo "$BODY" | python3 -c "import json,sys; print(json.load(sys.stdin).get('races',0))" 2>/dev/null || echo "0")
  # 0件は毎分出るのでログに書かない（大半の分は対象なし）
  [ "$RACES" != "0" ] && log "記録: $BODY"
else
  log "ERROR: スナップショット失敗 HTTP=$HTTP_CODE body=$(echo "$BODY" | head -c 200)"
fi
