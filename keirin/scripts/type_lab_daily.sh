#!/usr/bin/env bash
# 型ラボの日次バッチ（検証用・**入稿しない**）。
#   - 当日ぶんの買い目を組む（mode=live）
#   - 前日ぶんを採点する（当日の遅い開催が翌朝に確定するため）
#   - 当日ぶんも採点する（朝の時点で終わっているレースを取りこぼさない）
# 既存の keirin バッチとは独立で、書き込むのは keirin.type_lab_picks だけ。
#
# ⚠️ 当日ぶんの採点は**これだけでは足りない**。レースは一日中終わり続けるので、
#    別に `type_lab_settle.sh` を15分ごとに回している（cron 参照）。
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
echo "[type_lab] $(date '+%F %T') settle $TODAY"
"$PY" scripts/settle_type_lab_picks.py --date "$TODAY"
