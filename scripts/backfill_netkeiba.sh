#!/bin/bash
# 化けた netkeiba データの埋め戻し — 夜間
#
# 中身は backend/scripts/backfill_netkeiba.py。
# 2026-09-06 に見つけた文字化けのうち、**netkeiba にしか無い情報**を取り直す。
# 馬名と中央の血統は JV-Link / UmaConn から書き戻し済み（現在 0 件）。
#
# 🔴 **状態を持たない。** 対象は「いま化けている行」から毎回引き直すので、
#   途中で止まっても次回そのまま続きから走る。予算時間で切ってよい。
#
# 🔴 **なぜ夜間なのか**
#   レートリミッタは時間帯別で、深夜（0-6時）が最も緩い（min_interval 3.0s）。
#   日中（9-15時）は 5.0s で、同じ件数に 1.7 倍かかる。
#   ⚠️ 01:10 の backfill-netkeiba-time-index と重ならない時刻にすること。
#
# 🔴 **`--target time_index` は別枠**（化けではなく欠損の埋め戻し）
#   netkeiba-index が 2026-05 以降ずっと 10 分でタイムアウト kill されていたため
#   約 3,600 レースのタイム指数が欠けている（2026-09-07 実測 3,607・115 日ぶん）。
#   sekito 側の backfill-netkeiba-time-index (id=96) が担っていたが、
#   **そのジョブ自体も 10 分 kill されていて実質進んでいなかった**（初回 32 レースのみ）。
#   kiseki の cron には scheduler のタイムアウトが無いので予算がそのまま効く。
#
#   30 3 * * * .../backfill_netkeiba.sh --target time_index --minutes 110 >> .../logs/backfill_netkeiba.log 2>&1
#
# 残りが 0 になったら cron から外してよい。件数は次で見る:
#   docker exec galloplab-backend-1 uv run python /app/scripts/backfill_netkeiba.py --count
#   docker exec galloplab-backend-1 uv run python /app/scripts/backfill_netkeiba.py --target time_index --count
#
# VPS cron 設定（ホストは JST・一時的なジョブ）:
#   30 3 * * * /home/ysuzuki/GitHub/kiseki/scripts/backfill_netkeiba.sh --target all --minutes 110 >> /home/ysuzuki/GitHub/kiseki/logs/backfill_netkeiba.log 2>&1
#
# 終了コード: 0。IP 制限で中断したら 2。

set -u
CONTAINER="galloplab-backend-1"
LOCK="/tmp/backfill_netkeiba.lock"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1"; }

exec 9>"$LOCK"
if ! flock -n 9; then
  log "前回の実行がまだ動いています。今回はスキップします"
  exit 0
fi

log "=== backfill_netkeiba.sh 開始 ==="
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  log "ERROR: コンテナが起動していません: $CONTAINER"
  exit 1
fi

docker exec "$CONTAINER" uv run python /app/scripts/backfill_netkeiba.py "$@" 2>&1
RC=$?
log "=== backfill_netkeiba.sh 終了 (rc=$RC) ==="
exit "$RC"
