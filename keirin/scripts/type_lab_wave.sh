#!/usr/bin/env bash
# 型ラボの昼・夕の回（朝に入稿できなかったレースを拾い直す）。
#
#   使い方: scripts/type_lab_wave.sh {noon|evening} [YYYY-MM-DD]
#
#   cron 07:20  type_lab_daily.sh          … 組む → 入稿 → 採点（当日の全レース）
#   cron 13:00  type_lab_wave.sh noon      … ナイターの残り（1R 15時に間に合う）
#   cron 18:00  type_lab_wave.sh evening   … ミッドナイトの残り（1R 20時に間に合う）
#
# 🔴 **朝の時点で当日の全レースを対象にしている。** 型ラボは予測オッズだけで
#    買い目を組むので、既存ランクのように板が育つのを待つ理由が無い。
#    ここへ残るのは主に **並び予想・AI印が朝に未公開だったレース**
#    （`entry_health.missing_market_inputs` で見送ったもの）。
#
# 🔴 **拾い直すレースは買い目を組み直してから入稿する。** 欠測のまま朝に組まれた
#    行は「印なし＝最弱・ライン無し＝全員同ライン」と読まれており、そのまま
#    売ると壊れた買い目を売ることになる（2026-08-26 熊本7R の型）。
#    組み直しは `netkeirin_submit_type_lab.py` が **`--race-key` で名指し**して
#    行う（日全体を組み直すと、既に売ったレースの行まで UPSERT で書き換わる）。
set -euo pipefail

SESSION="${1:-}"
if [[ "$SESSION" != "noon" && "$SESSION" != "evening" ]]; then
  echo "usage: $0 {noon|evening} [YYYY-MM-DD]" >&2
  exit 2
fi

cd "$(dirname "$0")/.."
PY="${KEIRIN_PYTHON:-.venv/bin/python3}"
TODAY="${2:-$(date +%F)}"
LOG_DIR="data/logs"
mkdir -p "$LOG_DIR"

# --- KEIRIN_DB_URL 必須チェック ---
# 🔴 未設定だと `type_lab_picks` が読めず **黙って0件**で終わる。
#    `wave_submit_wt.sh` が同じ理由で明示的に落とすようにしてある。
if [[ -z "${KEIRIN_DB_URL:-}" ]]; then
  echo "[type_lab] [FATAL] KEIRIN_DB_URL が未設定です。type_lab_wave.sh (${SESSION}) を中断します。"
  PYTHONPATH=. "$PY" -c "
from src.notify.discord import send
send('🚨 **[type_lab_wave.sh] KEIRIN_DB_URL が未設定のため入稿を中断しました。**', channel='system')
" || true
  exit 1
fi

echo "[type_lab] $(date '+%F %T') === 型ラボ入稿（波: ${SESSION}） $TODAY ==="
"$PY" scripts/netkeirin_submit_type_lab.py "$TODAY" "$SESSION" \
  2>&1 | tee -a "$LOG_DIR/type_lab_${TODAY}.log"
