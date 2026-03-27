#!/bin/bash
# kiseki realtime start - 毎日9:00に実行。当日開催があればrealtime起動し、
# 最終レース発走時刻+1時間後に自動終了をスケジュールする。
# cron: 0 9 * * * /Users/ysuzuki/GitHub/kiseki/scripts/realtime_start.sh >> /Users/ysuzuki/GitHub/kiseki/logs/cron.log 2>&1

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOGPREFIX="[realtime_start $(date '+%Y-%m-%d %H:%M:%S')]"
TODAY=$(date '+%Y%m%d')

echo "$LOGPREFIX START (today=$TODAY)"

# 当日の開催チェック + 最終レース発走時刻取得
# post_timeが "0000" または NULL のレースは除外
LAST_POST_TIME=$(PGPASSWORD="aEBkrj45Id26" psql \
  -h sekito-stable.com -U hrdb_user -d hrdb -tA \
  -c "SELECT MAX(post_time) FROM keiba.races
      WHERE date = '$TODAY'
        AND post_time IS NOT NULL
        AND post_time != '0000';" 2>/dev/null || echo "")

if [ -z "$LAST_POST_TIME" ] || [ "$LAST_POST_TIME" = "" ]; then
    echo "$LOGPREFIX SKIP: 本日($TODAY)の開催なし (post_time未登録)"
    exit 0
fi

echo "$LOGPREFIX 最終レース発走時刻: $LAST_POST_TIME"

# 終了時刻 = 最終レース発走時刻 + 1時間
LAST_HH="${LAST_POST_TIME:0:2}"
LAST_MM="${LAST_POST_TIME:2:2}"
STOP_EPOCH=$(date -j -f "%Y%m%d %H%M" "${TODAY} ${LAST_HH}${LAST_MM}" "+%s" 2>/dev/null)
STOP_EPOCH=$((STOP_EPOCH + 3600))
NOW_EPOCH=$(date +%s)
SLEEP_SECS=$((STOP_EPOCH - NOW_EPOCH))

if [ "$SLEEP_SECS" -le 0 ]; then
    echo "$LOGPREFIX SKIP: 終了時刻(最終R+1h)が既に過ぎています"
    exit 0
fi

STOP_TIME=$(date -r "$STOP_EPOCH" '+%H:%M')
echo "$LOGPREFIX 自動終了予定: $STOP_TIME (${SLEEP_SECS}秒後)"

# JV-Link既に起動中なら追加起動しない
IS_RUNNING=$(prlctl exec "Windows 11" --current-user powershell -Command \
  "if (Get-Process python -ErrorAction SilentlyContinue) { 'running' } else { 'idle' }" \
  2>/dev/null | tr -d '\r\n')

if [ "$IS_RUNNING" = "running" ]; then
    echo "$LOGPREFIX SKIP: JV-Link agent already running"
    exit 0
fi

# realtimeをバックグラウンドで起動
echo "$LOGPREFIX Launching realtime mode on Windows..."
prlctl exec "Windows 11" --current-user powershell -Command "
  Start-Process -FilePath 'python' \`
    -ArgumentList 'jvlink_agent.py --mode realtime' \`
    -WorkingDirectory 'C:\kiseki\windows-agent' \`
    -WindowStyle Normal \`
    -PassThru
" 2>&1

# 終了をバックグラウンドでスケジュール
nohup bash -c "sleep ${SLEEP_SECS} && ${SCRIPT_DIR}/realtime_stop.sh" \
  >> /Users/ysuzuki/GitHub/kiseki/logs/cron.log 2>&1 &

echo "$LOGPREFIX Realtime started. Auto-stop scheduled at $STOP_TIME"
