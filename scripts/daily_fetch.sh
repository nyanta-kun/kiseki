#!/bin/bash
# kiseki daily fetch + 指数算出パイプライン
# JV-Linkからデータ取得 → DB反映 → 指数算出
#
# JVデータ提供タイミング（jvdata-spec.md）に基づくcron設定:
#   0 8  * * *   毎朝8:00 - 日次ベースライン（前日データ取込）
#   30 14 * * 1  月曜14:30 - 確定成績・DIFF・血統（仕様: 月曜14:00提供）
#   0 17  * * 4  木曜17:00 - 週末出馬表（仕様: 木曜16:30提供）
#   30 20 * * 4  木曜20:30 - DIFF・血統・スナップ（仕様: 木曜20:00提供）
#   30 12 * * 5  金曜12:30 - 土曜出馬表更新（仕様: 金曜12:00提供）
#   30 12 * * 6  土曜12:30 - 日曜出馬表更新（仕様: 土曜12:00提供）
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
  "ls /proc/*/cmdline 2>/dev/null | xargs -I{} sh -c 'cat {} 2>/dev/null | tr \"\0\" \" \"' | grep -cE 'calculate_indices' || true")

if [ "${CALC_RUNNING:-0}" -gt 0 ]; then
    echo "$LOG_PREFIX SKIP: calculate_indices already running (${CALC_RUNNING} process(es))"
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

# ── Step 4: 指数算出（2段階）────────────────────────────────────────────
# 4a: 直近14日分（成績確定済み）→ calculate_indices_range.py（race_results必須）
START_DATE=$(date -v-14d '+%Y%m%d' 2>/dev/null || date -d '14 days ago' '+%Y%m%d')
END_DATE=$(date '+%Y%m%d')

echo "$LOG_PREFIX [2/3] Calculating indices (confirmed): $START_DATE -> $END_DATE"
docker exec "$DOCKER_CONTAINER" sh -c \
  "cd /app && uv run python scripts/calculate_indices_range.py --start $START_DATE --end $END_DATE" 2>&1
echo "$LOG_PREFIX [2/3a] Confirmed race index calculation done"

# 4b: 翌日分（出馬表あり・成績なし）→ calculate_indices.py（race_results不要）
TOMORROW=$(date -v+1d '+%Y%m%d' 2>/dev/null || date -d 'tomorrow' '+%Y%m%d')
TOMORROW_RACE_COUNT=$(docker exec "$DOCKER_CONTAINER" sh -c "cd /app && uv run python -c \"
from src.db.session import engine
from sqlalchemy import text
with engine.connect() as c:
    r = c.execute(text(\\\"SELECT COUNT(*) FROM keiba.races WHERE date='$TOMORROW'\\\"))
    print(r.scalar())
\"" 2>/dev/null || echo "0")

if [ "${TOMORROW_RACE_COUNT:-0}" -gt 0 ]; then
    echo "$LOG_PREFIX [2/3b] Calculating indices for tomorrow ($TOMORROW): ${TOMORROW_RACE_COUNT} races"
    docker exec "$DOCKER_CONTAINER" sh -c \
      "cd /app && uv run python scripts/calculate_indices.py --date $TOMORROW" 2>&1
    echo "$LOG_PREFIX [2/3b] Tomorrow index calculation done"
else
    echo "$LOG_PREFIX [2/3b] No races found for tomorrow ($TOMORROW), skipping"
fi
echo "$LOG_PREFIX [2/3] Index calculation done"

# ── Step 4c: 推奨レース生成（翌日レースがある場合）─────────────────────────
if [ "${TOMORROW_RACE_COUNT:-0}" -gt 0 ]; then
    echo "$LOG_PREFIX [2c/3] Generating recommendations for tomorrow ($TOMORROW)..."
    docker exec "$DOCKER_CONTAINER" sh -c \
      "cd /app && uv run python scripts/calculate_recommendations.py $TOMORROW" 2>&1
    echo "$LOG_PREFIX [2c/3] Recommendation generation done"
else
    echo "$LOG_PREFIX [2c/3] No races for tomorrow ($TOMORROW), skipping recommendation"
fi

# ── Step 4d: 推奨結果更新（直近7日分の的中・払戻を反映）──────────────────
echo "$LOG_PREFIX [2d/3] Updating recommendation results (last 7 days)..."
docker exec "$DOCKER_CONTAINER" sh -c \
  "cd /app && uv run python scripts/update_recommendation_results.py" 2>&1
echo "$LOG_PREFIX [2d/3] Recommendation result update done"

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

# ── Step 6: realtimeモード起動（既存プロセスがなければ） ──────────────────
REALTIME_RUNNING=$(prlctl exec "Windows 11" --current-user powershell -Command \
  "if (Get-Process python -ErrorAction SilentlyContinue) { 'running' } else { 'idle' }" 2>/dev/null | tr -d '\r\n')

if [ "$REALTIME_RUNNING" = "running" ]; then
    echo "$LOG_PREFIX [realtime] Python already running, skipping realtime start"
else
    echo "$LOG_PREFIX [realtime] Starting realtime monitor..."
    prlctl exec "Windows 11" --current-user powershell -Command "
      Start-Process -FilePath 'cmd.exe' \`
        -ArgumentList '/c cd /d C:\kiseki\windows-agent && python jvlink_agent.py --mode realtime' \`
        -WindowStyle Hidden -PassThru
    " 2>&1
    echo "$LOG_PREFIX [realtime] Realtime monitor launched"
fi

echo "$LOG_PREFIX DONE"
