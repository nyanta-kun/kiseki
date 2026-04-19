#!/bin/bash
# 毎朝 daily フェッチ + 指数算出を Windows Agent へトリガー
# VPS cron から呼び出される: 0 21 * * * /home/ysuzuki/GitHub/kiseki/scripts/daily_trigger.sh
# 21:00 UTC = 06:00 JST（翌日出馬表が揃う時間帯）

BACKEND_URL="http://127.0.0.1:8003"  # VPS backend (galloplab-backend-1コンテナ)
LOG_FILE="/home/ysuzuki/GitHub/kiseki/logs/daily_trigger.log"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a "$LOG_FILE"
}

log "=== daily_trigger.sh 開始 ==="

# Windows Agent へ daily フェッチ コマンドをキュー投入
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$BACKEND_URL/api/agent/command" \
  -H "Content-Type: application/json" \
  -d '{"action": "daily", "params": {}}' \
  --max-time 10)

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | head -1)

if [ "$HTTP_CODE" = "200" ]; then
  log "daily コマンドをキュー投入: $BODY"
else
  log "ERROR: コマンド投入失敗 HTTP=$HTTP_CODE body=$BODY"
  exit 1
fi

log "=== daily_trigger.sh 完了 ==="
