#!/bin/bash
# DM 指数自動収集 (Mac LaunchAgent から定期実行される)
#
# 過去30日 〜 翌14日のうち、DM 未取得 (date, course) を自動検出して取得する。
# 中央レース情報 (RA/SE) が DB に入った直後から、対応する DM をスキャンして取得。
#
# 起動方法:
#   - LaunchAgent: ~/Library/LaunchAgents/com.kiseki.dm-auto-fetch.plist
#   - 手動: /Users/ysuzuki/GitHub/kiseki/scripts/dm_auto_fetch.sh
#
# 多重起動防止のためロックファイルを使用。

set -u

PROJECT_ROOT="/Users/ysuzuki/GitHub/kiseki"
BACKEND_DIR="$PROJECT_ROOT/backend"
PYTHON="$BACKEND_DIR/.venv/bin/python"
ORCHESTRATOR="$BACKEND_DIR/scripts/protocol_dm_orchestrator.py"
LOG_FILE="$PROJECT_ROOT/logs/dm_auto_fetch.log"
LOCK_FILE="/tmp/dm_auto_fetch.lock"

mkdir -p "$(dirname "$LOG_FILE")"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a "$LOG_FILE"
}

# ロック取得（既に動いているなら何もしないで終了）
if [ -e "$LOCK_FILE" ]; then
  PID=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    log "skip: already running (pid=$PID)"
    exit 0
  fi
  log "stale lock file found (pid=$PID), removing"
  rm -f "$LOCK_FILE"
fi

echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT INT TERM

log "=== dm_auto_fetch 開始 ==="

# orchestrator を --from-db で起動 (中央全10場)
# - 過去30日〜翌14日でDM未取得のレースを自動検出
# - importer タイムアウトは orchestrator 内で 1 時間
cd "$BACKEND_DIR" || {
  log "ERROR: cannot cd to $BACKEND_DIR"
  exit 1
}

"$PYTHON" "$ORCHESTRATOR" --from-db \
  --courses 01,02,03,04,05,06,07,08,09,10 \
  >> "$LOG_FILE" 2>&1
RC=$?

if [ "$RC" -eq 0 ]; then
  log "完了: rc=$RC"
else
  log "ERROR: rc=$RC"
fi

log "=== dm_auto_fetch 終了 ==="
exit "$RC"
