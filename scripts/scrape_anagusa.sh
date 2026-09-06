#!/bin/bash
# サラブレ「穴ぐさ」の取得 — 開催日（土日月）の朝
#
# 中身は backend/scripts/scrape_anagusa.py。
# 2026-09-06 の統合 Phase 2 で sekito の scripts_schedules id=21
# (`bin/scrape/anagusa` / `10 7 * * 6,0,1`) から移設した。
#
# 🔴 **なぜ 07:10 なのか（動かすと空振りする）**
#   穴ぐさは当日ぶんが朝に公開される。移設前から同じ時刻で回っており、
#   07:00 の scrape_supply_check.sh（前日ぶんの網羅率を見る）の直後にあたる。
#   ここを早めるとピックがまだ載っておらず、0 件で終わる。
#
# 🔴 **1 日 1 リクエストで全場ぶんが取れる**
#   場タブは CSS の切替でしかなく、初期 HTML に全場のパネルが同梱されている。
#   レースごとに叩く必要はない（叩くとサラブレ側に無駄な負荷をかける）。
#
# ⚠️ **ピックが 0 件でも正常終了する。**
#   このジョブは土日月に走るが、月曜はピックが出ない（2026-09-06 実測: 直近 60 日で
#   穴ぐさが入っている日は土 9 日 / 日 9 日のみ、月曜は 0 日）。0 件を異常にすると
#   毎週月曜に必ずエラーが出る。
#   「中央開催日なのに 0 件」の判定は check_scrape_supply.py が持っている。
#
# VPS cron 設定（ホストは JST）:
#   10 7 * * 6,0,1 /home/ysuzuki/GitHub/kiseki/scripts/scrape_anagusa.sh >> /home/ysuzuki/GitHub/kiseki/logs/scrape_anagusa.log 2>&1
#
# 手動実行:
#   /home/ysuzuki/GitHub/kiseki/scripts/scrape_anagusa.sh
#   /home/ysuzuki/GitHub/kiseki/scripts/scrape_anagusa.sh --date 2026-09-06 --dry-run
#
# 終了コード: 取得できたら 0。取得自体に失敗したら非 0。

set -u

CONTAINER="galloplab-backend-1"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1"; }

log "=== scrape_anagusa.sh 開始 ==="

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  log "ERROR: コンテナが起動していません: $CONTAINER"
  exit 1
fi

docker exec "$CONTAINER" uv run python /app/scripts/scrape_anagusa.py "$@" 2>&1
RC=$?

if [ "$RC" -eq 0 ]; then
  log "完了"
else
  log "異常終了: rc=$RC"
fi

log "=== scrape_anagusa.sh 終了 ==="
exit "$RC"
