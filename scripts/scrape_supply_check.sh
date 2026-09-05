#!/bin/bash
# 指数の上流（sekito スクレイプ供給）の死活監視 — 日次
#
# 中身は backend/scripts/check_scrape_supply.py。異常があれば Discord に通知する。
#
# 🔴 **なぜ 07:00 なのか（動かすと誤検知する）**
#   チェックごとに見る日が違い、07:00 だけが全部の条件を満たす:
#     - sekito の sync-jra-from-jvlink（06:00）**の後**
#       …これより前だと当日の発走時刻がまだ 00:00 で、毎朝かならず誤検知する
#     - netkeiba-index（08:30）**の前**
#       …前日ぶんの網羅率を、その日の実行に上書きされる前に見る
#     - 中央の初レース 09:50 **の前**
#       …発走時刻が壊れていたらパドックが空振りすることを、始まる前に知らせる
#   詳しくは check_scrape_supply.py の docstring「実行時刻と対象日の設計」。
#
# 🔴 **なぜ VPS で回せるか**
#   feature_health_weekly.sh は psycopg2 依存のため Mac に置かれているが、
#   このスクリプトは SQLAlchemy の SyncSessionLocal を使うので backend
#   コンテナでそのまま動く。上流の断はその日のうちに知りたいので日次。
#
# VPS cron 設定（ホストは JST。ログは cron 側のリダイレクトで一元管理）:
#   0 7 * * * /home/ysuzuki/GitHub/kiseki/scripts/scrape_supply_check.sh >> /home/ysuzuki/GitHub/kiseki/logs/scrape_supply_check.log 2>&1
#
# 手動実行:
#   /home/ysuzuki/GitHub/kiseki/scripts/scrape_supply_check.sh
#   /home/ysuzuki/GitHub/kiseki/scripts/scrape_supply_check.sh --dry-run   # 通知しない
#
# 終了コード: 異常なしで 0、WARN が 1 件でもあれば 1。

set -u

CONTAINER="galloplab-backend-1"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $1"
}

DRY_RUN="${1:-}"

log "=== scrape_supply_check.sh 開始 ==="

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  log "ERROR: コンテナが起動していません: $CONTAINER"
  exit 1
fi

if [ -n "$DRY_RUN" ]; then
  log "DRY-RUN モード（Discord へは送らない）"
  docker exec "$CONTAINER" uv run python /app/scripts/check_scrape_supply.py 2>&1
else
  docker exec "$CONTAINER" uv run python /app/scripts/check_scrape_supply.py --notify 2>&1
fi

RC=$?
if [ "$RC" -eq 0 ]; then
  log "完了: 異常なし"
else
  log "WARN あり: rc=$RC（詳細は上の出力・Discord を参照）"
fi

log "=== scrape_supply_check.sh 終了 ==="
exit "$RC"
