#!/bin/bash
# Mac mini の launchd ジョブ共通ラッパー
#
# 使い方: run_job.sh <ジョブ名> <コマンド> [引数...]
#
# 2026-03〜07 に daily_fetch.sh が一度も完走せず、誰も気づかなかった。
# 「動いていないことに気づけない」を構造で防ぐため、全ジョブをこれ経由で呼ぶ:
#   - PATH と秘密情報は _env.sh で明示する
#   - 出力は logs/jobs/<名前>.log に残す（握り潰さない・/tmp に置かない）
#   - 成功したら ~/.local/state/jobs/<名前>.ok を書く（ハートビート）
#     → 成功を記録しないジョブは死んでも気づけない。鮮度は jobs.py status で見る
#   - 失敗したら <名前>.fail を書き、Discord に通知する
#     通知は「成功→失敗」「失敗→成功」の変わり目だけ（30 秒毎のジョブで連投しない）
#   - 同名ジョブの多重起動はスキップする（macOS に flock(1) は無いので mkdir で排他）
#
# 環境変数:
#   RUN_JOB_QUIET=1  成功時の START/END 行を出さない（高頻度ジョブ用）
set -uo pipefail

if [ $# -lt 2 ]; then
  echo "使い方: $0 <ジョブ名> <コマンド> [引数...]" >&2
  exit 64
fi
NAME="$1"; shift

# shellcheck source=_env.sh
. "$(dirname "$0")/_env.sh" || exit 78
mkdir -p "$JOB_LOG_DIR" "$JOB_STATE_DIR"

LOG="$JOB_LOG_DIR/$NAME.log"
if [ -f "$LOG" ] && [ "$(stat -f %z "$LOG")" -gt 20971520 ]; then
  mv -f "$LOG" "$LOG.1"
fi
exec >>"$LOG" 2>&1

ts() { date '+%Y-%m-%d %H:%M:%S'; }

notify() {
  local msg="$1"
  if [ -z "${DISCORD_WEBHOOK_URL_SYSTEM:-}" ]; then
    echo "[$(ts)] WARN: DISCORD_WEBHOOK_URL_SYSTEM 未設定のため通知できません: $msg"
    return 0
  fi
  /usr/bin/python3 -c 'import json,sys; print(json.dumps({"content": sys.argv[1][:1900]}))' "$msg" \
    | curl -sS -m 15 -H 'Content-Type: application/json' -d @- "$DISCORD_WEBHOOK_URL_SYSTEM" \
    || echo "[$(ts)] WARN: Discord 通知に失敗"
}

LOCK="$JOB_STATE_DIR/$NAME.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  OLD_PID="$(cat "$LOCK/pid" 2>/dev/null || true)"
  if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "[$(ts)] SKIP $NAME: 前回実行 (pid=$OLD_PID) が継続中"
    exit 0
  fi
  rm -rf "$LOCK"
  mkdir "$LOCK" || exit 75
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT

QUIET="${RUN_JOB_QUIET:-0}"
START=$(date +%s)
[ "$QUIET" = 1 ] || echo "===== [$(ts)] START $NAME: $*"

"$@"
RC=$?

DUR=$(( $(date +%s) - START ))
HOST="$(scutil --get LocalHostName 2>/dev/null || hostname -s)"
if [ "$RC" -eq 0 ]; then
  [ "$QUIET" = 1 ] || echo "===== [$(ts)] END $NAME rc=0 (${DUR}s)"
  printf '%s\t%ss\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$DUR" > "$JOB_STATE_DIR/$NAME.ok"
  if [ -f "$JOB_STATE_DIR/$NAME.fail" ]; then
    rm -f "$JOB_STATE_DIR/$NAME.fail"
    notify "✅ [$HOST] $NAME 復旧"
  fi
else
  echo "===== [$(ts)] END $NAME rc=$RC (${DUR}s)"
  WAS_FAILING=0
  [ -f "$JOB_STATE_DIR/$NAME.fail" ] && WAS_FAILING=1
  printf '%s\trc=%s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$RC" > "$JOB_STATE_DIR/$NAME.fail"
  if [ "$WAS_FAILING" = 0 ]; then
    notify "🔴 [$HOST] $NAME 失敗 rc=$RC (${DUR}s)
\`\`\`
$(tail -n 15 "$LOG" | cut -c1-200)
\`\`\`
ログ: $LOG"
  fi
fi
exit "$RC"
