#!/usr/bin/env bash
# 型ラボの日次バッチ（検証用・**入稿しない**）。
#   - 当日ぶんの買い目を組む（7車 mode=live / 9車 mode=live9）
#   - 前日ぶんを採点する（当日の遅い開催が翌朝に確定するため）
#   - 当日ぶんも採点する（朝の時点で終わっているレースを取りこぼさない）
#
# 🔴 **7車と9車は別々に1回ずつ回す**（2026-08-28 に9車を追加）。同じ mode へ
#    混ぜないのは、同じ plan_key でも配当帯が 2〜3倍違うため。9車は型F を
#    決勝の F_hit だけに絞る（`src/type_lab.plans_for`）ので約 5.6件/日。
# 採点（`settle_type_lab_picks.py`）は mode を見ないので 9車も同じ経路で埋まる。
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
# 🔴 9車の失敗で**採点まで止めない**（`set -e` はここで打ち切る）。9車は
#    `data/models/odds_tf_n9.txt` が要るので、配布漏れがあると必ずここで落ちる。
#    その日の7車の採点を巻き添えにしないよう、失敗は記録して先へ進む。
echo "[type_lab] $(date '+%F %T') build live9 $TODAY"
if ! "$PY" scripts/build_type_lab_picks.py --mode live --date "$TODAY" --n-entries 9; then
  echo "[type_lab] ⚠️ 9車の生成に失敗（7車の採点は続行する）"
fi
echo "[type_lab] $(date '+%F %T') settle $YEST"
"$PY" scripts/settle_type_lab_picks.py --date "$YEST"
echo "[type_lab] $(date '+%F %T') settle $TODAY"
"$PY" scripts/settle_type_lab_picks.py --date "$TODAY"
