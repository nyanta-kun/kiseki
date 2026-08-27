#!/usr/bin/env bash
# 型ラボの当日採点（1時間ごと・検証用）。
#
# 🔴 レースは一日中終わり続けるので、**日次バッチ1回だけでは当日の結果が
#    翌朝まで画面に出ない**（2026-08-27 に「確定した結果が反映されない」と
#    指摘を受けた）。着順と確定オッズが入ったものから順に埋める。
# 何度流しても害はない（採点は `settled_at IS NULL` の行だけを対象にする）。
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${KEIRIN_PYTHON:-.venv/bin/python3}"
"$PY" scripts/settle_type_lab_picks.py --date "$(date +%F)"
