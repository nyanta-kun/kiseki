#!/bin/bash
# netkeiba のタイム指数・調教・データ分析 — 日次
#
# 中身は backend/scripts/scrape_netkeiba_index.py。
# 2026-09-07 の統合 Phase 2 で sekito の scripts_schedules id=63
# (`bin/scrape/netkeiba-index` / `30 8 * * *`) から移設した。
#
# 🔴 **打ち切らないこと。**
#   2026-09-06 実測で 79 レース / 24.8 分（194 リクエスト・7.7 秒/件）。所要時間の
#   ほとんどはレートリミッタの待ちで、短くする唯一の方法は取得項目を減らすこと。
#   sekito 側は scheduler の既定タイムアウト 10 分で毎日 kill されており、処理順が
#   中央→地方のため **2026-05 以降ずっと地方が全滅**していた（9/5 も 10.0 分で
#   failed、中央35/地方21 しか取れていない）。cron からは timeout を掛けない。
#
# 🔴 **なぜ 08:30 なのか**
#   07:00 の scrape_supply_check.sh（前日ぶんの網羅率を見る）の後、中央の初レース
#   09:50 の前。ここを動かすと監視の前提もずれる。
#
# 取得するもの（1 レースあたり）:
#   タイム指数 speed.html    中央・地方   調教 oikiri.html  中央のみ
#   データ分析 data_top.html 中央・地方（sekito のレース詳細 UI が使う）
#   → 中央 3 / 地方 2 リクエスト
#
# VPS cron 設定（ホストは JST）:
#   30 8 * * * /home/ysuzuki/GitHub/kiseki/scripts/scrape_netkeiba_index.sh >> /home/ysuzuki/GitHub/kiseki/logs/scrape_netkeiba_index.log 2>&1
#
# 終了コード: 取得できたか既に取得済みなら 0。IP 制限で中断したら 2。

set -u
CONTAINER="galloplab-backend-1"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1"; }

log "=== scrape_netkeiba_index.sh 開始 ==="
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  log "ERROR: コンテナが起動していません: $CONTAINER"
  exit 1
fi

docker exec "$CONTAINER" uv run python /app/scripts/scrape_netkeiba_index.py "$@" 2>&1
RC=$?
[ "$RC" -eq 0 ] && log "完了" || log "異常終了: rc=$RC"
log "=== scrape_netkeiba_index.sh 終了 ==="
exit "$RC"
