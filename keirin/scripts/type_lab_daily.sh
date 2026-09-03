#!/usr/bin/env bash
# 型ラボの朝バッチ。
#   1. 当日ぶんの買い目を組む（7車 mode=live / 9車 mode=live9）
#   2. **netkeirin へ入稿する**（2026-08-28 の本番移行から。それ以前は検証のみ）
#   3. 前日ぶんを採点する（当日の遅い開催が翌朝に確定するため）
#   4. 当日ぶんも採点する（朝の時点で終わっているレースを取りこぼさない）
#
# 🔴 **組む → 入稿する の順序をこのファイルで固定する。** 以前は入稿（07:00 の
#    `daily_picks_wt.sh`）が型ラボの生成（07:15）より**先**に走っていた。
#    型ラボを既存の入稿経路へ相乗りさせていたら、当日の `type_lab_picks` が
#    まだ無い状態で入稿が走り、**毎朝0件のまま誰も気づかない**ところだった。
#    入稿を同じスクリプトの中に置けば、この順序は構造的に壊れない。
#
# 🔴 **7車と9車は別々に1回ずつ組む**（2026-08-28 に9車を追加）。同じ mode へ
#    混ぜないのは、同じ plan_key でも配当帯が 2〜3倍違うため。9車は型F を
#    決勝の F_pay だけに絞る（`src/type_lab.plans_for`）。
# 入稿は7車・9車をまとめて1回で流す（`mode IN ('live','live9')` を読むため）。
# 採点（`settle_type_lab_picks.py`）は mode を見ないので 9車も同じ経路で埋まる。
#
# ⚠️ 当日ぶんの採点は**これだけでは足りない**。レースは一日中終わり続けるので、
#    別に `type_lab_settle.sh` を15分ごとに回している（RUNBOOK の cron 参照）。
# ⚠️ 朝に入稿できなかったレース（並び予想・AI印が未公開など）は
#    `type_lab_wave.sh` が昼・夕に拾い直す。
set -euo pipefail
cd "$(dirname "$0")/.."
# 🔴 VPS の keirin バッチは全て `.venv/bin/python3` を使う（`daily_picks_wt.sh` と同じ）。
#    素の python3 だと依存が入っておらず import で落ちる。
PY="${KEIRIN_PYTHON:-.venv/bin/python3}"
TODAY="$(date +%F)"
YEST="$(date -d '1 day ago' +%F 2>/dev/null || date -v-1d +%F)"

echo "[type_lab] $(date '+%F %T') build live $TODAY  ($PY)"
"$PY" scripts/build_type_lab_picks.py --mode live --date "$TODAY"
# 🔴 9車の失敗で**入稿と採点まで止めない**（`set -e` はここで打ち切る）。9車は
#    `data/models/odds_tf_n9.txt` が要るので、配布漏れがあると必ずここで落ちる。
#    その日の7車を巻き添えにしないよう、失敗は記録して先へ進む。
echo "[type_lab] $(date '+%F %T') build live9 $TODAY"
if ! "$PY" scripts/build_type_lab_picks.py --mode live --date "$TODAY" --n-entries 9; then
  echo "[type_lab] ⚠️ 9車の生成に失敗（7車の入稿・採点は続行する）"
fi

# 🔴 入稿の失敗で採点を止めない。採点が落ちると前日の成績が画面に出ないまま
#    翌日を迎える（入稿の失敗より復旧が遅れる）。
echo "[type_lab] $(date '+%F %T') submit morning $TODAY"
if ! "$PY" scripts/netkeirin_submit_type_lab.py "$TODAY" morning; then
  echo "[type_lab] ⚠️ 入稿に失敗（採点は続行する）"
fi

# 🔴 **「自信あり」の選定は入稿スクリプトの中へ移した**（2026-09-04）。
#    入稿通知に「どのレースが自信ありか・発走は何時か」を出すため、
#    通知より前に選ぶ必要がある（ここで呼ぶと通知の**あと**になり必ず「なし」）。
#    実体は変わらず `scripts/pick_confident_race_wt.py::pick`（朝だけ・冪等）。
#    順序も変わっていない: 入稿 → 公開 → 選定 → 通知。
#    ⚠️ `daily_picks_wt.sh`（07:00）にも同じ呼び出しがあるが、あちらは型ラボの
#       入稿より前に走るので当日の型ラボの行をまだ1件も見られない。

echo "[type_lab] $(date '+%F %T') settle $YEST"
"$PY" scripts/settle_type_lab_picks.py --date "$YEST"
echo "[type_lab] $(date '+%F %T') settle $TODAY"
"$PY" scripts/settle_type_lab_picks.py --date "$TODAY"
