#!/bin/bash
# netkeiba の IP 制限の解除チェックと自動復旧 — 毎時
#
# 中身は backend/scripts/check_ip_restriction.py。
# 2026-09-08 の統合 Phase 2 で sekito の scripts_schedules id=34
# (`bin/maintenance/check-ip-restriction` / `0 * * * *`) から移設した。
#
# 制限フラグが立っていなければ**1 リクエストも出さずに終わる**ので、毎時でも安い。
# 立っていれば netkeiba へ 1 回だけアクセスして、通れればフラグを落とす。
#
# 🔴 **これが動いていないと、IP 制限から自力で戻れない。**
#   検出側（各スクレイパ）はフラグを立てるだけで、落とすのはこのジョブだけ。
#   止めると netkeiba 系ジョブが永久にスキップされ続ける。
#
# 🔴 **`scheduler_enabled` の後始末も兼ねる。**
#   移植版の検出処理は `scheduler_enabled` を false にしないが、過去に sekito 側が
#   落とした値が残っていることがある。`scheduler.js` の `runJob()` はこの値を見ない
#   ので普段は無害だが、**その状態で sekito のコンテナが再起動すると
#   `loadSchedules()` が何も読まず、スケジューラごと止まって自力で戻れなくなる。**
#
# VPS cron 設定（ホストは JST）:
#   0 * * * * /home/ysuzuki/GitHub/kiseki/scripts/check_ip_restriction.sh >> /home/ysuzuki/GitHub/kiseki/logs/check_ip_restriction.log 2>&1
#
# 手動:
#   .../check_ip_restriction.sh --status   # 状態を見るだけ（ネットワークへ出ない）
#   .../check_ip_restriction.sh --check    # アクセス可否だけ試す
#
# 終了コード: 常に 0。制限中は異常ではない（毎時アラートが鳴ると誰も見なくなる）。

set -u
CONTAINER="galloplab-backend-1"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1"; }

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  log "ERROR: コンテナが起動していません: $CONTAINER"
  exit 1
fi

docker exec "$CONTAINER" uv run python /app/scripts/check_ip_restriction.py "$@" 2>&1
exit $?
