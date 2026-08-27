#!/usr/bin/env bash
# 型ラボの日次バッチ（検証用・**入稿しない**）。
#   - 当日ぶんの買い目を組む（mode=live）
#   - 前日ぶんを採点する
# 既存の keirin バッチとは独立で、書き込むのは keirin.type_lab_picks だけ。
set -euo pipefail
cd "$(dirname "$0")/.."
# 🔴 VPS の keirin バッチは全て `.venv/bin/python3` を使う（`daily_picks_wt.sh` と同じ）。
#    素の python3 だと依存が入っておらず import で落ちる。
PY="${KEIRIN_PYTHON:-.venv/bin/python3}"
TODAY="$(date +%F)"
YEST="$(date -d '1 day ago' +%F 2>/dev/null || date -v-1d +%F)"
echo "[type_lab] $(date '+%F %T') build live $TODAY  ($PY)"
"$PY" scripts/build_type_lab_picks.py --mode live --date "$TODAY"
echo "[type_lab] $(date '+%F %T') settle $YEST"
"$PY" scripts/settle_type_lab_picks.py --date "$YEST"
