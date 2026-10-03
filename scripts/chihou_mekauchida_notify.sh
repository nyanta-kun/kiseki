#!/bin/bash
# メカウチダ地方の買い目 × 指数上位5位 一致の Discord 通知（10 分おき）
#
# VPS cron 設定（ホストは JST）:
#   */10 9-21 * * * /home/ysuzuki/GitHub/kiseki/scripts/chihou_mekauchida_notify.sh >> /home/ysuzuki/GitHub/kiseki/logs/chihou_mekauchida_notify.log 2>&1
#
# 地方はナイター（最終 20:50 前後）まであるので 21 時台まで回す。
# 開催の有無はスクリプト側で判定する（発走 1 時間前のレースが無ければサイトを見ない）。
# 通知は各レースの「発走前の最後の監視」（発走まで 10 分以内）の 1 回だけ。
# 🔴 間隔を変えるときは mekauchida_notify.py の POLL_INTERVAL も揃えること。
#
# 手動実行:
#   /home/ysuzuki/GitHub/kiseki/scripts/chihou_mekauchida_notify.sh --dry-run

set -u

CONTAINER="galloplab-backend-1"

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') [mekauchida_nar] ERROR: コンテナが起動していません: $CONTAINER"
  exit 1
fi

docker exec "$CONTAINER" uv run python /app/scripts/chihou_mekauchida_notify.py "$@" 2>&1
