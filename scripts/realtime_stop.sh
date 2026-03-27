#!/bin/bash
# kiseki realtime stop - 開催日夕方にrealtime agentを終了
# cron: 30 17 * * 6,0 /Users/ysuzuki/GitHub/kiseki/scripts/realtime_stop.sh >> /Users/ysuzuki/GitHub/kiseki/logs/cron.log 2>&1

set -euo pipefail
LOGPREFIX="[realtime_stop $(date '+%Y-%m-%d %H:%M:%S')]"

echo "$LOGPREFIX START"

prlctl exec "Windows 11" --current-user powershell -Command \
  "Stop-Process -Name python -Force -ErrorAction SilentlyContinue; Write-Output 'stopped'" 2>/dev/null || true

echo "$LOGPREFIX Realtime agent stopped"
