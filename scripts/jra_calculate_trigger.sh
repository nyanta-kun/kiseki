#!/bin/bash
# JRA指数算出トリガー
#
# 使い方:
#   jra_calculate_trigger.sh                # 当日 JST
#   jra_calculate_trigger.sh tomorrow       # 翌日 JST（前夜実行用）
#   jra_calculate_trigger.sh 20260503       # 指定日
#
# 既存の daily_trigger.sh も $TOMORROW 分を計算するが、
# 障害時の冗長化として独立した cron として用意。
#
# VPS cron 推奨設定:
#   0 13 * * * /home/ysuzuki/GitHub/kiseki/scripts/jra_calculate_trigger.sh tomorrow
#     # 13:00 UTC = 22:00 JST 前夜 → 翌日分を算出
#
# 指数算出は version-based upsert で冪等のため二重実行は無害。

set -u

BACKEND_URL="http://127.0.0.1:8003"
LOG_FILE="/home/ysuzuki/GitHub/kiseki/logs/jra_calculate_trigger.log"
ENV_FILE="/home/ysuzuki/GitHub/kiseki/.env"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a "$LOG_FILE"
}

API_KEY=$(grep '^CHANGE_NOTIFY_API_KEY=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
if [ -z "$API_KEY" ]; then
  log "ERROR: CHANGE_NOTIFY_API_KEY が .env に見つかりません"
  exit 1
fi

# 引数解釈
ARG="${1:-}"
case "$ARG" in
  "")
    DATE=$(TZ=Asia/Tokyo date '+%Y%m%d')
    LABEL="今日"
    ;;
  tomorrow)
    DATE=$(TZ=Asia/Tokyo date -d 'tomorrow' '+%Y%m%d' 2>/dev/null \
        || TZ=Asia/Tokyo date -v+1d '+%Y%m%d')
    LABEL="翌日"
    ;;
  yesterday)
    DATE=$(TZ=Asia/Tokyo date -d 'yesterday' '+%Y%m%d' 2>/dev/null \
        || TZ=Asia/Tokyo date -v-1d '+%Y%m%d')
    LABEL="昨日"
    ;;
  [0-9]*)
    if [[ "$ARG" =~ ^[0-9]{8}$ ]]; then
      DATE="$ARG"
      LABEL="指定日"
    else
      log "ERROR: 不正な日付形式: $ARG (YYYYMMDD で指定)"
      exit 1
    fi
    ;;
  *)
    log "ERROR: 不明な引数: $ARG"
    exit 1
    ;;
esac

log "=== jra_calculate_trigger.sh 開始 date=$DATE ($LABEL) ==="

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
  "$BACKEND_URL/api/import/calculate?date=$DATE" \
  -H "X-API-Key: $API_KEY" \
  --max-time 600)

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | head -n -1)

if [ "$HTTP_CODE" = "200" ]; then
  log "指数算出キック成功 (バックグラウンド処理): date=$DATE"
  log "  → 算出は数分かかる。完了確認は backend ログ '[calculate] 完了' を参照"
else
  log "ERROR: 指数算出キック失敗 HTTP=$HTTP_CODE date=$DATE body=$(echo "$BODY" | head -c 200)"
  exit 1
fi

log "=== jra_calculate_trigger.sh 完了 ==="
