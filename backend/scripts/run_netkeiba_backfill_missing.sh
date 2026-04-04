#!/usr/bin/env bash
# netkeiba 未収集月バックフィル実行スクリプト
#
# 対象: 2025-05 〜 2024-01（既収集の 2025-06〜2026-03 はスキップ）
#
# バックグラウンド実行（VPS上で直接）:
#   cd /app
#   nohup bash scripts/run_netkeiba_backfill_missing.sh > /tmp/backfill_all.log 2>&1 &
#
# または Mac から:
#   ssh sekito 'docker exec galloplab-backend-1 bash -c \
#     "cd /app && nohup bash scripts/run_netkeiba_backfill_missing.sh \
#      > /tmp/backfill_all.log 2>&1 &"'
#
# ログ確認:
#   ssh sekito 'docker exec galloplab-backend-1 tail -30 /tmp/backfill_all.log'

set -euo pipefail

MONTHS=(
  202505 202504 202503 202502 202501
  202412 202411 202410 202409 202408 202407 202406 202405 202404 202403 202402 202401
)

LOG_DIR=/tmp
SCRIPT=/app/scripts/run_netkeiba_backfill.py

echo "====================================="
echo " netkeiba バックフィル（未収集分）"
echo " 対象: ${#MONTHS[@]} ヶ月"
echo " 開始: $(date '+%Y-%m-%d %H:%M:%S')"
echo "====================================="

TOTAL_STORED=0

for YYYYMM in "${MONTHS[@]}"; do
  LOG_FILE="${LOG_DIR}/backfill_${YYYYMM}.log"
  echo ""
  echo "--------------------------------------"
  echo " ${YYYYMM:0:4}年${YYYYMM:4:2}月  開始: $(date '+%H:%M:%S')"
  echo "--------------------------------------"

  if PYTHONPATH=/app uv run python "$SCRIPT" --year-month "$YYYYMM" 2>&1 | tee "$LOG_FILE"; then
    STORED=$(grep -oP '合計 \K[0-9,]+(?= ペア格納)' "$LOG_FILE" | tr -d ',' | tail -1 || echo 0)
    echo " ✅ 完了: ${STORED} ペア格納"
    TOTAL_STORED=$((TOTAL_STORED + STORED))
  else
    echo " ❌ エラーで停止。ログ: ${LOG_FILE}"
    echo " ※ 再実行すると取得済みをスキップして再開できます"
    exit 1
  fi

  sleep 5
done

echo ""
echo "====================================="
echo " バックフィル完了"
echo " 合計格納: ${TOTAL_STORED} ペア"
echo " 終了: $(date '+%Y-%m-%d %H:%M:%S')"
echo "====================================="
