#!/bin/bash
# kiseki daily fetch + 指数算出パイプライン
# JV-Linkからデータ取得 → DB反映 → 直近14日分の指数を算出
#
# cron設定（毎朝8:00）:
#   0 8 * * * /Users/ysuzuki/GitHub/kiseki/scripts/daily_fetch.sh >> /Users/ysuzuki/GitHub/kiseki/logs/cron.log 2>&1
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

# ── Step 2: 指数算出バッチの多重起動チェック ──────────────────────────
CALC_RUNNING=$(docker exec "$DOCKER_CONTAINER" sh -c \
  "ls /proc/*/cmdline 2>/dev/null | xargs -I{} sh -c 'cat {} 2>/dev/null | tr \"\0\" \" \"' | grep -c calculate_indices_range || true")

if [ "${CALC_RUNNING:-0}" -gt 0 ]; then
    echo "$LOG_PREFIX SKIP: calculate_indices_range already running (${CALC_RUNNING} process(es))"
    exit 0
fi

# ── Step 3: Windows側でデータ取得 (--mode daily) ─────────────────────────
echo "$LOG_PREFIX [1/3] Launching daily mode on Windows..."
prlctl exec "Windows 11" --current-user powershell -Command "
  Start-Process -FilePath 'cmd.exe' \`
    -ArgumentList '/c cd /d C:\kiseki\windows-agent && python jvlink_agent.py --mode daily' \`
    -WindowStyle Normal \`
    -Wait
" 2>&1
echo "$LOG_PREFIX [1/3] Windows agent done"

# ── Step 4: 直近14日分の指数を算出 ────────────────────────────────────────
START_DATE=$(date -v-14d '+%Y%m%d' 2>/dev/null || date -d '14 days ago' '+%Y%m%d')
END_DATE=$(date '+%Y%m%d')

echo "$LOG_PREFIX [2/3] Calculating indices: $START_DATE -> $END_DATE"
docker exec "$DOCKER_CONTAINER" sh -c \
  "cd /app && uv run python scripts/calculate_indices_range.py --start $START_DATE --end $END_DATE" 2>&1
echo "$LOG_PREFIX [2/3] Index calculation done"

# ── Step 5: 完了サマリー ──────────────────────────────────────────────────
COMPOSITE_VERSION=$(docker exec "$DOCKER_CONTAINER" sh -c \
  "cd /app && uv run python -c 'from src.indices.composite import COMPOSITE_VERSION; print(COMPOSITE_VERSION)'" 2>/dev/null || echo "?")

echo "$LOG_PREFIX [3/3] Checking v${COMPOSITE_VERSION} count..."
docker exec "$DOCKER_CONTAINER" sh -c "cd /app && uv run python -c \"
from src.db.session import engine
from sqlalchemy import text
from src.indices.composite import COMPOSITE_VERSION
with engine.connect() as conn:
    r = conn.execute(text(f'SELECT COUNT(*) FROM keiba.calculated_indices WHERE version={COMPOSITE_VERSION}'))
    print(f'v{COMPOSITE_VERSION} total:', r.scalar())
\"" 2>&1 || true

echo "$LOG_PREFIX DONE"
