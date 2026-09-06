#!/bin/bash
# パドック到着後の指数引き直しトリガー
#
# 🔴 **なぜ必要か（2026-09-06 の実測で発覚）。**
#   パドック評価は各レースの発走20分前に netkeiba へ出る。一方、指数は
#   前夜 22:00 と当日 07:30 のバッチでしか算出されない。
#   つまり **パドックが指数に入る経路が構造的に存在しなかった**。
#     9/6 実測: パドック到着 09:42〜10:33 / 指数の算出 00:03〜00:49
#     → composite v6 の paddock_index は 491頭すべて 50.0（sd=0）のままだった。
#   上流のスクレイプを直しても、これが無いと指数には反映されない。
#
# 対象は「パドックがその馬の最後の算出より後に届いたレース」だけ。
# 対象が無ければ抽出クエリ1本で終わるので、空振りは安い。
#
# VPS cron 設定（パドックウォッチャー id=64 と同じ曜日・時間帯に合わせる）:
#   */5 9-17 * * 6,0,1 /home/ysuzuki/GitHub/kiseki/scripts/jra_paddock_refresh_trigger.sh >> /home/ysuzuki/GitHub/kiseki/logs/jra_paddock_refresh_trigger.log 2>&1
#
#   パドックは T-20分 に届き、ウォッチャーは3分間隔なので、5分間隔なら
#   おおむね T-15分 までに指数へ反映される。発走前に間に合う。
#
# 使い方:
#   jra_paddock_refresh_trigger.sh            # 当日 JST
#   jra_paddock_refresh_trigger.sh 20260906   # 指定日
#
# 指数算出は version-based upsert で冪等なので、二重実行は無害。

set -u

BACKEND_URL="http://127.0.0.1:8003"
ENV_FILE="/home/ysuzuki/GitHub/kiseki/.env"

# ⚠️ ここで tee -a しないこと。cron 側でもログファイルへリダイレクトしているため、
#    tee すると全行が二重に記録される（jra_calculate_trigger.log が実際そうなっている）。
log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $1"
}

API_KEY=$(grep '^CHANGE_NOTIFY_API_KEY=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
if [ -z "$API_KEY" ]; then
  log "ERROR: CHANGE_NOTIFY_API_KEY が .env に見つかりません"
  exit 1
fi

ARG="${1:-}"
if [ -z "$ARG" ]; then
  DATE=$(TZ=Asia/Tokyo date '+%Y%m%d')
elif echo "$ARG" | grep -qE '^[0-9]{8}$'; then
  DATE="$ARG"
else
  log "ERROR: 不明な引数: $ARG（YYYYMMDD か無指定）"
  exit 1
fi

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
  "$BACKEND_URL/api/import/calculate-paddock-refresh?date=$DATE" \
  -H "X-API-Key: $API_KEY" \
  --max-time 60)

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | head -n -1)

if [ "$HTTP_CODE" = "200" ]; then
  # 毎回ログを出すと5分ごとに膨れるので、キック成功は静かに済ませる。
  # 実際に引き直したかどうかは backend ログの '[paddock-refresh]' を見る。
  exit 0
fi

log "ERROR: パドック引き直しキック失敗 HTTP=$HTTP_CODE date=$DATE body=$(echo "$BODY" | head -c 200)"
exit 1
