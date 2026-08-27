#!/usr/bin/env bash
# 型ラボの当日採点（検証用）。
#
# 🔴 レースは一日中終わり続けるので、**日次バッチ1回だけでは当日の結果が
#    翌朝まで画面に出ない**（2026-08-27 に「確定した結果が反映されない」と
#    指摘を受けた）。着順と確定オッズが入ったものから順に埋める。
# 何度流しても害はない（採点は `settled_at IS NULL` の行だけを対象にする）。
#
# 🔴 **前日ぶんも必ず流す**（2026-08-27 追加）。ミッドナイトの最終レースは
#    23:20〜23:30 発走で、確定着順が入るのは日付が変わった後。当日ぶんだけを
#    見ていると、00 時以降の実行は `date +%F` が翌日を指すため
#    **その日の最後の数レースが翌朝 07:15 の日次バッチまで埋まらない**。
#
# 🔴 呼び出し間隔は `intraday_results_wt.sh`（*/15）に合わせること。
#    着順・確定オッズを入れているのはそちらで、毎時1回だと最大 60 分遅れる
#    （/keirin は 15 分で更新されるので「型ラボだけ古い」と見える）。
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${KEIRIN_PYTHON:-.venv/bin/python3}"
TODAY="$(date +%F)"
YEST="$(date -d '1 day ago' +%F 2>/dev/null || date -v-1d +%F)"
"$PY" scripts/settle_type_lab_picks.py --date "$YEST"
"$PY" scripts/settle_type_lab_picks.py --date "$TODAY"
