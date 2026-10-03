#!/bin/bash
# メカウチダの買い目 × 指数上位5位 / 穴ぐさ 一致の Discord 通知（10 分おき）
#
# VPS cron 設定（ホストは JST）:
#   */10 8-17 * * * /home/ysuzuki/GitHub/kiseki/scripts/mekauchida_notify.sh >> /home/ysuzuki/GitHub/kiseki/logs/mekauchida_notify.log 2>&1
#
# 開催日の判定はスクリプト側で行う（今日の中央のレースが発走 1 時間前に入っていなければ
# サイトを見ずに 0 件で終わる）ので、cron に曜日条件は要らない。
# 通知は各レースの「発走前の最後の監視」（発走まで 10 分以内）の 1 回だけ。
# 🔴 間隔を変えるときは mekauchida_notify.py の POLL_INTERVAL も揃えること
#    （揃っていないと最後の監視が 0 回または 2 回になる）。
#
# 手動実行:
#   /home/ysuzuki/GitHub/kiseki/scripts/mekauchida_notify.sh --dry-run

set -u

CONTAINER="galloplab-backend-1"

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') [mekauchida] ERROR: コンテナが起動していません: $CONTAINER"
  exit 1
fi

docker exec "$CONTAINER" uv run python /app/scripts/mekauchida_discord_notify.py "$@" 2>&1
