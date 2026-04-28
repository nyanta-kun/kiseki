#!/bin/bash
# 地方競馬指数算出トリガー（realtime 0:00跨ぎイベント不在時の保険）
#
# VPS cron 推奨設定（21:30 UTC = 06:30 JST 当日。Routine 09:00 JST 発火の2.5h前）:
#   30 21 * * * /home/ysuzuki/GitHub/kiseki/scripts/chihou_calculate_trigger.sh
#
# umaconn_agent realtime が稼働中（0:00跨ぎイベントで自動算出）でも実行されるが、
# 指数算出は version-based upsert で冪等のため二重実行は無害。
# realtime が落ちている朝でも当日指数を確実に算出する。

set -u

BACKEND_URL="http://127.0.0.1:8003"
LOG_FILE="/home/ysuzuki/GitHub/kiseki/logs/chihou_calculate_trigger.log"
ENV_FILE="/home/ysuzuki/GitHub/kiseki/.env"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a "$LOG_FILE"
}

API_KEY=$(grep '^CHANGE_NOTIFY_API_KEY=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
if [ -z "$API_KEY" ]; then
  log "ERROR: CHANGE_NOTIFY_API_KEY が .env に見つかりません"
  exit 1
fi

# 当日 JST（cron は UTC で動くため明示的に TZ 指定）
DATE=$(TZ=Asia/Tokyo date '+%Y%m%d')

log "=== chihou_calculate_trigger.sh 開始 date=$DATE ==="

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
  "$BACKEND_URL/api/import/chihou/calculate?date=$DATE" \
  -H "X-API-Key: $API_KEY" \
  --max-time 600)

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | head -n -1)

if [ "$HTTP_CODE" = "200" ]; then
  log "指数算出キック成功 (バックグラウンド処理): date=$DATE"
  log "  → 算出は数分かかる。完了確認は backend ログ '[chihou calculate] 完了' を参照"
else
  log "ERROR: 指数算出キック失敗 HTTP=$HTTP_CODE date=$DATE body=$(echo "$BODY" | head -c 200)"
  exit 1
fi

log "=== chihou_calculate_trigger.sh 完了 ==="
