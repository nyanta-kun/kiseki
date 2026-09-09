#!/bin/bash
# POG 指名馬の出走想定・枠順確定を Discord へ通知する
#
# 中身は backend/scripts/notify_pog_entries.py。
# 2026-09-10 の統合 Phase 5(5f) で sekito の 2 ジョブから移設した。
#
#   旧 id=95  bin/notify/pog-weekly-entries  30 19 * * 3  → --mode entry --weekend
#   旧 id=94  bin/notify/pog --barrier       0 13 * * *   → --mode barrier --tomorrow
#
# 🔴 **sekito の id=94 / id=95 と同時に動かしてはいけない。** 重複判定の表が別
#   （sekito.pog_notifications と keiba.pog_notifications）なので二重に届く。
#   切替は「sekito を止める → こちらを有効にする」の順。
#   ⚠️ sekito のスケジューラは DB をポーリングしない。is_enabled=false の後
#      `docker restart sekito-backend-1` まで行うこと。
#
# 🔴 **なぜ水曜 19:30 と毎日 13:00 なのか**
#   出走想定（entry）は確定出馬表が出る前に送るもので、`keiba.projected_entries`
#   が水曜までに揃う。枠順（barrier）は前日 11:30 頃に JRA が公開し、12:00 の
#   daily_trigger が keiba.races / race_entries へ取り込むので 13:00 に翌日ぶんを送る。
#   ここを前倒しすると「枠順が入っていない」通知になる。
#
# VPS cron 設定（ホストは JST）:
#   30 19 * * 3 /home/ysuzuki/GitHub/kiseki/scripts/notify_pog_entries.sh --mode entry --weekend >> .../logs/notify_pog_entries.log 2>&1
#   0 13 * * *  /home/ysuzuki/GitHub/kiseki/scripts/notify_pog_entries.sh --mode barrier --tomorrow >> .../logs/notify_pog_entries.log 2>&1
#
# 手動実行:
#   /home/ysuzuki/GitHub/kiseki/scripts/notify_pog_entries.sh --mode entry --date 2026-09-12 --dry-run
#
# 終了コード: 0 = 正常（送信 0 件も正常）。1 = 送信に失敗したものがある。

set -u
CONTAINER="galloplab-backend-1"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1"; }

log "=== notify_pog_entries.sh 開始 ($*) ==="
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  log "ERROR: コンテナが起動していません: $CONTAINER"
  exit 1
fi

docker exec -w /app "$CONTAINER" /app/.venv/bin/python scripts/notify_pog_entries.py "$@" 2>&1
RC=$?
[ "$RC" -eq 0 ] && log "完了" || log "異常終了: rc=$RC"
exit "$RC"
