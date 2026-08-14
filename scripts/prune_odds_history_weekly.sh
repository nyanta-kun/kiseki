#!/bin/bash
# keiba.odds_history の週次刈り込み（LaunchAgent com.kiseki.jra-odds-prune から実行）
#
# 何を消すかは backend/scripts/prune_odds_history.py の docstring を参照:
#   post   … 発走後に書かれた行（realtime の空回り。読み手が無い）
#   exotic … exotic 券種の発走前・最終スナップショット以外
# win/place の発走前時系列には触らない（前向き記録・odds 系分析が使う）。
#
# 🔴 **なぜ Mac から回すか**
#   VPS の backend は Docker で動いており、コンテナに psycopg2 が入っていない
#   （アプリは asyncpg を使う）。スクリプトを SQL に書き写すと「消す条件」の正本が
#   2 つになるので、**既存の Python スクリプトをそのまま使える Mac 側**に置く。
#   Mac は毎日 03:15 に自動起床する（pmset repeat）。
#
# 🔴 **なぜ月曜 05:00 か**
#   - 中央は土日開催。月曜なら直前の週末ぶんが「発走後」として刈れる
#   - 日次 DB バックアップ（03:30〜04:25）の**直後**に置く。削除は取り消せないので、
#     常に「削除より新しいバックアップ」ではなく「削除の直前のバックアップ」がある状態にする
#   - JRA realtime は動いていない時間帯
#
# 週次で回す理由: 一度きりの実行では発走後の行がまた溜まる（週 約1.5GB ペース）。

set -u

REPO="/Users/ysuzuki/GitHub/kiseki"
BACKEND="$REPO/backend"
PY="$BACKEND/.venv/bin/python"
LOG_FILE="$REPO/logs/prune_odds_history.log"
LOCK="/tmp/prune_odds_history.lock"
BACKUP_DIR="$HOME/kiseki-backups/daily"

# 何日前までを対象にするか。週次なので 30 日あれば数週間の欠落も吸収できる。
# 期間を絞るのは、全期間（4,400 開催日）を舐めると無駄な往復が増えるため。
LOOKBACK_DAYS=30

log() {
  mkdir -p "$(dirname "$LOG_FILE")"
  echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$LOG_FILE"
}

# 多重起動防止（前回が長引いていたら見送る。次週で拾える）
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
# その場合スクリプトが存在せず Python のトレースバックになるので、先に明示的に落とす。
if [ ! -f "$BACKEND/scripts/prune_odds_history.py" ]; then
  log "ERROR: $BACKEND/scripts/prune_odds_history.py がありません"
  log "  → $REPO が main 以外のブランチになっている可能性があります"
  log "  → git -C $REPO branch --show-current で確認してください"
  exit 1
fi

# 🔴 削除は取り消せない。直近のバックアップが無ければ実行しない。
LATEST_DUMP=$(ls -t "$BACKUP_DIR"/hrdb-keiba-*.dump 2>/dev/null | head -1)
if [ -z "$LATEST_DUMP" ]; then
  log "ERROR: keiba のバックアップが見つかりません ($BACKUP_DIR)。刈り込みを中止"
  exit 1
fi
# 36時間以上古いバックアップしか無ければ、バックアップ側が壊れている可能性がある
if [ -n "$(find "$LATEST_DUMP" -mmin +2160 2>/dev/null)" ]; then
  log "ERROR: 最新バックアップが古すぎます ($LATEST_DUMP)。刈り込みを中止"
  exit 1
fi

START=$(date -v-${LOOKBACK_DAYS}d '+%Y%m%d' 2>/dev/null || date -d "-${LOOKBACK_DAYS} days" '+%Y%m%d')
log "=== 開始 start=$START (backup: $(basename "$LATEST_DUMP")) ==="

cd "$BACKEND" || { log "ERROR: cd 失敗"; exit 1; }
OUT=$("$PY" scripts/prune_odds_history.py --start "$START" --execute --sleep 0.2 2>&1)
RC=$?

echo "$OUT" | grep -E "現在の総行数|削除完了|ERROR|Traceback" | while read -r line; do
  log "  $line"
done

if [ "$RC" -ne 0 ]; then
  log "ERROR: rc=$RC"
  log "$(echo "$OUT" | tail -5)"
fi
log "=== 終了 rc=$RC ==="
exit "$RC"
