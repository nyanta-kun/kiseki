#!/bin/bash
# 吉馬（kichiuma.net / kichiuma-chiho.net）の SP 能力値取得
#
# 中身は backend/scripts/scrape_kichiuma.py。
# 2026-09-06 の統合 Phase 2 で sekito の scripts_schedules id=22 と id=92
# (`bin/scrape/kichiuma` / `30 0 * * *` と `30 6 * * *`) から移設した。
#
# 🔴 **なぜ 1 日 2 回なのか（片方を消すと地方が欠ける）**
#   00:30 の回は地方の出走表が揃う前に走る。移設前は sekito の地方供給同期
#   (06:05) より前だったため佐賀・門別などを取りこぼしており、06:30 の回が
#   その補完だった。移設で供給が `chihou.races` 直読みになったので事情は
#   変わりうるが、UmaConn 側の取り込み時刻に依存するため**まず 2 回のまま
#   移して実測する**。取得済みは should_fetch でスキップされるので、
#   2 回目が無駄打ちになることはない。
#
# ⚠️ 1 レース 1 リクエスト。中央 36 + 地方 43 = 約 80 リクエスト（2026-09-06 実測）。
#   リクエスト間に 0.5〜2.0 秒のジッタが入るので 1 回あたり 2〜3 分かかる。
#
# VPS cron 設定（ホストは JST）:
#   30 0,6 * * * /home/ysuzuki/GitHub/kiseki/scripts/scrape_kichiuma.sh >> /home/ysuzuki/GitHub/kiseki/logs/scrape_kichiuma.log 2>&1
#
# 手動実行:
#   /home/ysuzuki/GitHub/kiseki/scripts/scrape_kichiuma.sh
#   /home/ysuzuki/GitHub/kiseki/scripts/scrape_kichiuma.sh --date 2026-09-06 --course JHSN --race 1 --dry-run
#   /home/ysuzuki/GitHub/kiseki/scripts/scrape_kichiuma.sh --only nar
#
# 終了コード: 1 件でも成功したら 0。対象が有ったのに全滅したら 1。

set -u

CONTAINER="galloplab-backend-1"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1"; }

log "=== scrape_kichiuma.sh 開始 ==="

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  log "ERROR: コンテナが起動していません: $CONTAINER"
  exit 1
fi

docker exec "$CONTAINER" uv run python /app/scripts/scrape_kichiuma.py "$@" 2>&1
RC=$?

if [ "$RC" -eq 0 ]; then
  log "完了"
else
  log "異常終了: rc=$RC"
fi

log "=== scrape_kichiuma.sh 終了 ==="
exit "$RC"
