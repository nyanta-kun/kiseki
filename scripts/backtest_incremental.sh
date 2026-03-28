#!/usr/bin/env bash
# backtest_incremental.sh
#
# 2ヶ月単位で遡りながら指数算出→バックテストを繰り返す
# 使い方:
#   ./scripts/backtest_incremental.sh                   # 2024-11-01〜2024-12-31 から遡る
#   ./scripts/backtest_incremental.sh 2024-09-01        # 任意の開始月から遡る
#
# 処理順序（例）:
#   2024-11-01 〜 2024-12-31  →  算出 → バックテスト
#   2024-09-01 〜 2024-10-31  →  算出 → バックテスト
#   ...
#   2023-01-01 〜 2023-02-28  →  算出 → バックテスト

set -euo pipefail

CONTAINER="kiseki-backend-1"
REPORT_DIR="/app/docs/verification"
LOG_DIR="/tmp/backtest_incremental"

# 遡る最終日（これ以前のデータまで算出する）
# 引数で先頭月を指定可能: YYYY-MM-DD
START_FROM="${1:-2024-12-31}"

# 遡る最古の月
OLDEST="2023-01-01"

echo "======================================================"
echo " 2ヶ月単位 増分バックテスト"
echo " 開始: ${START_FROM} から遡って ${OLDEST} まで"
echo "======================================================"

# 中間ログディレクトリ作成
docker exec "${CONTAINER}" bash -c "mkdir -p ${LOG_DIR} ${REPORT_DIR}"

# 日付を「期間の末日」から2ヶ月ずつ遡る
# period_end: 対象期間の末日（YYYY-MM-DD）
period_end="${START_FROM}"

while true; do
    # period_end の月初を求める
    ym=$(date -j -f "%Y-%m-%d" "${period_end}" "+%Y-%m" 2>/dev/null || date -d "${period_end}" "+%Y-%m")
    year=${ym%-*}
    month=${ym#*-}

    # 2ヶ月前の月初を算出
    month2=$((10#${month} - 1))  # 末日月-1 = 2ヶ月ブロックの先頭月
    if [ "${month2}" -le 0 ]; then
        month2=$((month2 + 12))
        year=$((year - 1))
    fi
    period_start=$(printf "%04d-%02d-01" "${year}" "${month2}")

    # OLDEST より前なら打ち切り
    if [[ "${period_start}" < "${OLDEST}" ]]; then
        period_start="${OLDEST}"
    fi

    # YYYYMMDD 形式に変換
    start_ymd="${period_start//-/}"
    end_ymd="${period_end//-/}"

    echo ""
    echo "------------------------------------------------------"
    echo " 期間: ${period_start} 〜 ${period_end}"
    echo "------------------------------------------------------"

    # ── 1. 指数算出 ──────────────────────────────────────
    CALC_LOG="${LOG_DIR}/calc_${start_ymd}_${end_ymd}.log"
    echo "[$(date '+%H:%M:%S')] 指数算出 開始..."
    docker exec "${CONTAINER}" bash -c \
        "cd /app && uv run python3 scripts/calculate_indices_range.py \
         --start ${start_ymd} --end ${end_ymd} \
         > ${CALC_LOG} 2>&1" \
    && echo "[$(date '+%H:%M:%S')] 指数算出 完了" \
    || { echo "[ERROR] 算出失敗。ログ: ${CALC_LOG}"; break; }

    # 算出件数をサマリー表示
    calc_count=$(docker exec "${CONTAINER}" bash -c \
        "grep -c '算出完了' ${CALC_LOG} || echo 0" 2>/dev/null || echo "?")
    echo "         → ${calc_count} レース算出"

    # ── 2. バックテスト ───────────────────────────────────
    echo "[$(date '+%H:%M:%S')] バックテスト 開始..."
    docker exec "${CONTAINER}" bash -c \
        "cd /app && uv run python3 scripts/backtest.py \
         --start ${start_ymd} --end ${end_ymd} \
         --report ${REPORT_DIR}/" \
    && echo "[$(date '+%H:%M:%S')] バックテスト 完了" \
    || echo "[WARN] バックテスト失敗（データ不足の可能性）"

    # ── 次の期間へ ────────────────────────────────────────
    if [[ "${period_start}" == "${OLDEST}" ]]; then
        echo ""
        echo "======================================================"
        echo " 全期間の算出・バックテスト完了"
        echo "======================================================"
        break
    fi

    # period_end を period_start の前日に移動
    # macOS: date -j -v-1d, Linux: date -d "-1 day"
    period_end=$(date -j -v-1d -f "%Y-%m-%d" "${period_start}" "+%Y-%m-%d" 2>/dev/null \
              || date -d "${period_start} -1 day" "+%Y-%m-%d")
done

echo ""
echo "レポート一覧:"
docker exec "${CONTAINER}" bash -c "ls -lt ${REPORT_DIR}/*backtest*.md 2>/dev/null | head -20"
