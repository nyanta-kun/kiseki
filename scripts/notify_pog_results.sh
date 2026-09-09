#!/bin/bash
# POG 指名馬のレース結果を Discord へ通知する
#
# 中身は backend/scripts/notify_pog_results.py。
# 2026-09-09 の統合 Phase 5(5f) で sekito の scripts_schedules id=93
# (`bin/notify/pog-result` / `*/10 10-23 * * *`) から移設した。
#
# 🔴 **sekito の id=93 と同時に動かしてはいけない。** 重複判定の表が別
#   （sekito.pog_notifications と keiba.pog_notifications）なので、両方動くと
#   同じ結果が二度届く。切替は「sekito を止める → こちらを有効にする」の順。
#   ⚠️ sekito のスケジューラは DB をポーリングしない。is_enabled=false にした後
#      `docker restart sekito-backend-1` まで行うこと。
#
# 🔴 **移設で直ったこと**: 2025年度グループの結果通知は **2026-05-02 で止まって
#   いた**（sekito.entries の凍結と同時期）。移設元は馬名で v_entries と突合して
#   おり、そこが凍結すると静かに 0 件になる。以後 77 件の着順が通知されていない。
#   こちらは netkeiba_horse_id で race_results を直読みするので影響を受けない。
#   ⚠️ 過去 77 件は**送らない**（いまさら通知しても混乱するだけ）。当日ぶんから。
#
# ⚠️ 10 分ごとに走る。1 回あたり数百 ms。送るものが無ければ何もしない。
#
# VPS cron 設定（ホストは JST）:
#   */10 10-23 * * * /home/ysuzuki/GitHub/kiseki/scripts/notify_pog_results.sh >> /home/ysuzuki/GitHub/kiseki/logs/notify_pog_results.log 2>&1
#
# 手動実行:
#   /home/ysuzuki/GitHub/kiseki/scripts/notify_pog_results.sh --dry-run
#   /home/ysuzuki/GitHub/kiseki/scripts/notify_pog_results.sh --date 2026-09-06 --dry-run
#
# 終了コード: 0 = 正常（送信 0 件も正常）。1 = 送信に失敗したものがある。

set -u
CONTAINER="galloplab-backend-1"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1"; }

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  log "ERROR: コンテナが起動していません: $CONTAINER"
  exit 1
fi

docker exec -w /app "$CONTAINER" /app/.venv/bin/python scripts/notify_pog_results.py "$@" 2>&1
RC=$?
# 送るものが無い回が大半なので、正常時はログを増やさない（10分ごとに走るため）
[ "$RC" -ne 0 ] && log "異常終了: rc=$RC"
exit "$RC"
