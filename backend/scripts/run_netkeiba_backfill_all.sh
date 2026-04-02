#!/usr/bin/env bash
# netkeiba 過去データ全期間バックフィル実行スクリプト
#
# 2026-02 〜 2024-01 まで月単位で順に遡ってスクレイピングを実行する。
# レート制限（429/403）で停止した場合は同じ月を再実行すると再開可能
# （取得済みレースは自動スキップ）。
#
# 使い方（Dockerコンテナ内で実行）:
#   docker exec kiseki-backend-1 bash /app/scripts/run_netkeiba_backfill_all.sh
#
# または Mac から:
#   docker exec kiseki-backend-1 bash -c "cd /app && bash scripts/run_netkeiba_backfill_all.sh"
#
# バックグラウンド実行（推奨）:
#   docker exec kiseki-backend-1 bash -c "
#     nohup bash /app/scripts/run_netkeiba_backfill_all.sh \
#       > /tmp/backfill_all.log 2>&1 &
#     echo PID=\$!"
#
# ログ確認:
#   docker exec kiseki-backend-1 tail -f /tmp/backfill_all.log

set -euo pipefail

# 2026-03 〜 2024-01 まで降順（取得済みレースは自動スキップされる）
MONTHS=(
  202603 202602 202601
  202512 202511 202510 202509 202508 202507 202506 202505 202504 202503 202502 202501
  202412 202411 202410 202409 202408 202407 202406 202405 202404 202403 202402 202401
)

LOG_DIR=/tmp
SCRIPT=/app/scripts/run_netkeiba_backfill.py

echo "====================================="
echo " netkeiba バックフィル 全期間実行"
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

  if uv run python "$SCRIPT" --year-month "$YYYYMM" 2>&1 | tee "$LOG_FILE"; then
    # 格納ペア数をログから抽出
    STORED=$(grep -oP '合計 \K[0-9,]+(?= ペア格納)' "$LOG_FILE" | tr -d ',' | tail -1 || echo 0)
    echo " ✅ 完了: ${STORED} ペア格納"
    TOTAL_STORED=$((TOTAL_STORED + STORED))
  else
    echo " ❌ エラーで停止。ログ: ${LOG_FILE}"
    echo " ※ 再実行すると取得済みをスキップして再開できます"
    exit 1
  fi

  # 月間インターバル（セッション管理のため少し間隔を置く）
  sleep 5
done

echo ""
echo "====================================="
echo " 全期間バックフィル完了"
echo " 合計格納: ${TOTAL_STORED} ペア"
echo " 終了: $(date '+%Y-%m-%d %H:%M:%S')"
echo "====================================="
