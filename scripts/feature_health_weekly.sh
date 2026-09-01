#!/bin/bash
# 指数（モデル特徴量）の死活監視 — 中央 + 地方（LaunchAgent com.kiseki.feature-health から実行）
#
# 🔴 **なぜ自動化するか**
#   `check_feature_health.py` / `check_chihou_feature_health.py` は 2026-09-01 時点で
#   **cron / LaunchAgent / CI のどこからも呼ばれていなかった**（参照 0 件を実測）。
#   手順書に書いてあるだけで、実行するかどうかは人の記憶に依存していた。
#
#   このリポジトリはまさにその型で失敗している:
#     - 調教データの日次取得が 2026-06-07 を最後に止まり、DB の最終日が
#       2026-08-11 のまま**誰も気づかなかった**（開催週の調教が丸ごと欠落）
#     - paddock_index の上流スクレイプが 2026-05 に停止し、以後ずっと定数 50
#   どちらも「検査スクリプトは存在したが動いていなかった」。
#
# 🔴 **なぜ Mac から回すか**
#   VPS の backend コンテナには psycopg2 が入っていない（アプリは asyncpg）。
#   prune_odds_history_weekly.sh と同じ理由で、既存の Python スクリプトを
#   そのまま使える Mac 側に置く。Mac は毎日 03:15 に自動起床する（pmset repeat）。
#
# 🔴 **なぜ週次か**
#   上の2件はどちらも**2か月以上**気づかれなかった。月次だと最悪 1 か月遅れる。
#   実体は DB への読み取りクエリだけなので週次でも負荷は小さい。
#   月曜 06:00 は日次バックアップ（03:30〜04:25）と odds 刈り込み（月曜 05:00）の後。
#
# 異常があれば **exit 1**。`launchctl list com.kiseki.feature-health` の
# LastExitStatus と、下記ログの `ERROR:` 行で気づけるようにする
# （dm_auto_fetch.sh と同じ流儀）。

set -u

REPO="/Users/ysuzuki/GitHub/kiseki"
BACKEND="$REPO/backend"
PY="$BACKEND/.venv/bin/python"
LOG_FILE="$REPO/logs/feature_health.log"
LOCK="/tmp/feature_health.lock"

log() {
  mkdir -p "$(dirname "$LOG_FILE")"
  echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG_FILE"
}

# 多重起動防止（前回が長引いていたら見送る。翌週で拾える）
if [ -e "$LOCK" ]; then
  if kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then
    log "skip: already running (pid=$(cat "$LOCK"))"
    exit 0
  fi
  rm -f "$LOCK"
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

if [ ! -x "$PY" ]; then
  log "ERROR: venv が見つかりません: $PY"
  exit 1
fi

# ⚠️ このクローンは他セッションが feature ブランチへ切り替えていることがある。
#    その場合スクリプトが存在せず Python のトレースバックになるので先に落とす。
for s in check_feature_health.py check_chihou_feature_health.py; do
  if [ ! -f "$BACKEND/scripts/$s" ]; then
    log "ERROR: $BACKEND/scripts/$s がありません（ブランチ切り替え中の可能性）"
    exit 1
  fi
done

log "=== feature health check start ==="
RC=0

for pillar in jra chihou; do
  case "$pillar" in
    jra)    script="check_feature_health.py" ;;
    chihou) script="check_chihou_feature_health.py" ;;
  esac
  out="$(cd "$BACKEND" && "$PY" "scripts/$script" 2>&1)"
  rc=$?
  # 出力は全文残す。DEAD/SHIFT/SPARSE がどの特徴量かを後から追えるようにする。
  printf '%s\n' "$out" >> "$LOG_FILE"
  if [ "$rc" -ne 0 ]; then
    log "ERROR: $pillar の特徴量に異常あり (rc=$rc) — 上の出力を確認してください"
    RC=1
  else
    log "ok: $pillar"
  fi
done

log "=== feature health check end (rc=$RC) ==="
exit "$RC"
