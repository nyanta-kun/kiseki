#!/bin/bash
# keiba / chihou のレースを sekito.kaisai + sekito.races へ供給する
#
# 中身は backend/scripts/sync_sekito_races.py。
# 2026-09-09 の統合 Phase 5(5b-3) で sekito の scripts_schedules id=76
# (`bin/run/sync-jra-from-jvlink` / `0 6 * * *`) と id=88
# (`bin/run/sync-nar-from-umaconn` / `5 6 * * *`) から移設した。
#
# 🔴 **「不要」と判断して止めたら本番が止まった。二度と止めないこと。**
#   統合 Phase 2（2026-09-07）で「供給同期は不要（kiseki は keiba/chihou を
#   直読みする）」と判断して両ジョブを無効化した。これは **kiseki 側の
#   スクレイパについてだけ**正しく、**sekito 自身の消費者**を見落としていた。
#
#   2026-09-09 実測:
#     sekito.races  9/8 まで有り → 9/9・9/10・9/11 が 0 件
#                   （chihou.races には 44 / 47 / 35 件ある）
#     sekito /api/races?date=2026-09-09 → {"venues":[]}
#     POG の出走通知は `FROM v_entries JOIN races r` で sekito.races を見ている
#
#   つまり **sekito のサイトと POG の出走通知が静かに空になっていた**。
#   `sekito.v_races` も実体は `sekito.races` のビューなので同じ穴に落ちる。
#
#   移設元の docstring 自身が同型の事故を記録している（2026-05-10 以降テーブルが
#   凍結し、スクレイパが毎日「対象レースがありません」で 0 件終了していた）。
#   **同じ穴を 4 か月後にもう一度掘った。**
#
# 🔴 **なぜ 06:00 なのか**
#   07:00 の scrape_supply_check.sh（前日ぶんの網羅率）より前で、
#   08:30 の netkeiba-index が対象レースを列挙するより前。ここを後ろへ動かすと
#   下流が「対象レースなし」で静かに 0 件終了する。
#
# ⚠️ JRA と NAR を 1 本にまとめてある（移設元は 0:6 と 5:6 の 2 本）。
#   どちらも数百 ms で終わる SQL なので分ける理由が無い。
#
# VPS cron 設定（ホストは JST）:
#   0 6 * * * /home/ysuzuki/GitHub/kiseki/scripts/sync_sekito_races.sh >> /home/ysuzuki/GitHub/kiseki/logs/sync_sekito_races.log 2>&1
#
# 手動実行:
#   /home/ysuzuki/GitHub/kiseki/scripts/sync_sekito_races.sh
#   /home/ysuzuki/GitHub/kiseki/scripts/sync_sekito_races.sh --from 2026-09-09 --to 2026-09-11
#   /home/ysuzuki/GitHub/kiseki/scripts/sync_sekito_races.sh --only nar
#
# 終了コード: 0 = 正常 / 1 = 例外。

set -u
CONTAINER="galloplab-backend-1"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1"; }

log "=== sync_sekito_races.sh 開始 ==="
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  log "ERROR: コンテナが起動していません: $CONTAINER"
  exit 1
fi

docker exec -w /app "$CONTAINER" /app/.venv/bin/python scripts/sync_sekito_races.py "$@" 2>&1
RC=$?
[ "$RC" -eq 0 ] && log "完了" || log "異常終了: rc=$RC"
log "=== sync_sekito_races.sh 終了 ==="
exit "$RC"
