#!/bin/bash
# kiseki daily fetch + 指数算出パイプライン
# JV-Linkからデータ取得 → DB反映 → 直近14日分の指数を算出
#
# cron設定例（月〜火 8:00 = 週末レース結果を翌週月曜に処理）:
#   0 8 * * 1,2 /Users/ysuzuki/GitHub/kiseki/scripts/daily_fetch.sh >> /Users/ysuzuki/GitHub/kiseki/logs/cron.log 2>&1
#
# 手動実行:
#   bash /Users/ysuzuki/GitHub/kiseki/scripts/daily_fetch.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_PREFIX="[daily_fetch $(date '+%Y-%m-%d %H:%M:%S')]"
DOCKER_CONTAINER="kiseki-backend-1"

echo "$LOG_PREFIX START"

# ── Step 1: JV-Link プロセス多重起動チェック ────────────────────────────
IS_RUNNING=$(prlctl exec "Windows 11" --current-user powershell -Command \
  "if (Get-Process python -ErrorAction SilentlyContinue) { 'running' } else { 'idle' }" 2>/dev/null | tr -d '\r\n')

if [ "$IS_RUNNING" = "running" ]; then
    echo "$LOG_PREFIX SKIP: JV-Link agent already running"
    exit 0
fi

# ── Step 2: Windows側でデータ取得 (--mode daily) ─────────────────────────
echo "$LOG_PREFIX [1/3] Launching daily mode on Windows..."
prlctl exec "Windows 11" --current-user powershell -Command "
  Start-Process -FilePath 'cmd.exe' \`
    -ArgumentList '/c cd /d C:\kiseki\windows-agent && python jvlink_agent.py --mode daily' \`
    -WindowStyle Normal \`
    -Wait
" 2>&1
echo "$LOG_PREFIX [1/3] Windows agent done"

# ── Step 3: 直近14日分の指数を算出 ────────────────────────────────────────
START_DATE=$(date -v-14d '+%Y%m%d' 2>/dev/null || date -d '14 days ago' '+%Y%m%d')
END_DATE=$(date '+%Y%m%d')

echo "$LOG_PREFIX [2/3] Calculating indices: $START_DATE -> $END_DATE"
docker exec "$DOCKER_CONTAINER" bash -c \
  "cd /app && uv run python3 scripts/calculate_indices_range.py --start $START_DATE --end $END_DATE" 2>&1
echo "$LOG_PREFIX [2/3] Index calculation done"

# ── Step 4: 完了サマリー ──────────────────────────────────────────────────
echo "$LOG_PREFIX [3/3] Checking latest v3 count..."
docker exec "$DOCKER_CONTAINER" bash -c "cd /app && uv run python3 -c \"
from src.db.session import engine
from sqlalchemy import text
with engine.connect() as conn:
    r = conn.execute(text(\\\"SELECT COUNT(*) FROM keiba.calculated_indices WHERE version=3\\\"))
    print('v3 total:', r.scalar())
\" 2>/dev/null" 2>&1 || true

echo "$LOG_PREFIX DONE"
