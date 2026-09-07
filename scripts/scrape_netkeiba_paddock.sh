#!/bin/bash
# パドックの発走前ウォッチャー — 3 分ごと
#
# 中身は backend/scripts/scrape_netkeiba_paddock.py。
# 2026-09-07 の統合 Phase 2 で sekito の scripts_schedules id=64
# (`bin/scrape/netkeiba-paddock` / `*/3 9-17 * * 6,0,1`) から移設した。
#
# 発走 20 分以内・未取得の中央レースだけを取る。対象が無ければ 1 リクエストも
# 出さずに終わるので、空振りは安い（実測 0.1 秒）。
#
# 🔴 **この移設で 2 つの不具合が直っている**（2026-09-06 実測）
#   1. 文字コード — 移設元は paddock.html を EUC-JP 決め打ちで読んでいたが実際は
#      UTF-8。馬名・寸評・評価「穴」が化け、しかも**正しい馬名を上書きしていた**
#      （当日の中央でパドック取得あり 192/192 行が化け）。
#   2. 発走時刻 — 移設元は `sekito.races.start_time` を見ており、日次同期の
#      `ON CONFLICT DO NOTHING` で 00:00 に固定されていたため「発走20分以内」に
#      永久に一致せず、パドック指数が 7 月以降ずっと 0 件だった。
#      kiseki は `keiba.races.post_time` を直接読む。
#
# ⚠️ 3 分ごとなので、多重起動しないよう flock で守る。1 回が長引いても次が重ならない。
#
# VPS cron 設定（ホストは JST）:
#   */3 9-17 * * 6,0,1 /home/ysuzuki/GitHub/kiseki/scripts/scrape_netkeiba_paddock.sh >> /home/ysuzuki/GitHub/kiseki/logs/scrape_netkeiba_paddock.log 2>&1
#
# 終了コード: 0（対象 0 件でも 0）。IP 制限で中断したら 2。

set -u
CONTAINER="galloplab-backend-1"
LOCK="/tmp/scrape_netkeiba_paddock.lock"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1"; }

exec 9>"$LOCK"
if ! flock -n 9; then
  log "前回の実行がまだ動いています。今回はスキップします"
  exit 0
fi

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  log "ERROR: コンテナが起動していません: $CONTAINER"
  exit 1
fi

docker exec "$CONTAINER" uv run python /app/scripts/scrape_netkeiba_paddock.py "$@" 2>&1
exit $?
