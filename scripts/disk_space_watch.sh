#!/usr/bin/env bash
# Mac の空き容量の監視（LaunchAgent com.kiseki.disk-space-watch から 30 分ごとに呼ぶ）
#
# 空きが閾値を切ったら通知センターへ出す。
#
# なぜ要るか（2026-11-10 に実際に発生）:
#   Mac の空きが 358MB まで落ち、Parallels が Windows VM を凍結した
#   （PRL_WARN_TOO_LOW_HDD_SIZE の確認を出して応答待ちになる）。
#   VM の中の realtime も watchdog も止まるので、地方のオッズと結果が
#   06:22 から 17:49 まで 1 件も取れなかった。prlctl list は「running」と
#   表示し続け、Windows 側からは何も検知できない。Mac 側で先に気づくしかない。
#
# 使い方:
#   scripts/disk_space_watch.sh                       # 通常実行
#   DISK_WATCH_WARN_GB=30 scripts/disk_space_watch.sh  # 閾値を変える
#   DISK_WATCH_AVAIL_KB=1000000 scripts/disk_space_watch.sh  # 空きを偽装して通知を試す

set -uo pipefail

VOLUME="${DISK_WATCH_VOLUME:-/System/Volumes/Data}"
WARN_GB="${DISK_WATCH_WARN_GB:-15}"   # これを切ったら警告（最大 3 時間に 1 回）
CRIT_GB="${DISK_WATCH_CRIT_GB:-5}"    # これを切ったら毎回通知
WARN_INTERVAL_SEC="${DISK_WATCH_WARN_INTERVAL_SEC:-10800}"
LOG_FILE="${DISK_WATCH_LOG:-${HOME}/GitHub/kiseki/logs/disk_space_watch.log}"
STAMP_FILE="${DISK_WATCH_STAMP:-${HOME}/.cache/kiseki/disk_space_watch.last_warn}"

mkdir -p "$(dirname "$LOG_FILE")" "$(dirname "$STAMP_FILE")"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

notify() {
  local title="$1" body="$2" sound="$3"
  osascript -e "display notification \"${body}\" with title \"${title}\" sound name \"${sound}\"" \
    >/dev/null 2>&1 || log "WARN: 通知を出せなかった（osascript 失敗）"
}

if [ -n "${DISK_WATCH_AVAIL_KB:-}" ]; then
  avail_kb="$DISK_WATCH_AVAIL_KB"
else
  # df -k の 4 列目 = 空き (KiB)。APFS はコンテナ共有なので Data ボリュームの値で足りる
  avail_kb=$(df -k "$VOLUME" 2>/dev/null | awk 'NR==2 {print $4}')
fi

if ! [[ "$avail_kb" =~ ^[0-9]+$ ]]; then
  log "ERROR: 空き容量を読めなかった volume=$VOLUME value='$avail_kb'"
  notify "kiseki ディスク監視" "空き容量を読めませんでした。${LOG_FILE} を確認" "Basso"
  exit 1
fi

avail_gb=$(awk -v kb="$avail_kb" 'BEGIN {printf "%.1f", kb / 1024 / 1024}')
warn_kb=$((WARN_GB * 1024 * 1024))
crit_kb=$((CRIT_GB * 1024 * 1024))

if [ "$avail_kb" -lt "$crit_kb" ]; then
  log "CRIT: 空き ${avail_gb}GB（${CRIT_GB}GB 未満）"
  notify "kiseki ディスク残りわずか" \
    "空き ${avail_gb}GB。このままだと Windows VM が凍結し、オッズ取得が止まります" "Basso"
  date +%s > "$STAMP_FILE"
elif [ "$avail_kb" -lt "$warn_kb" ]; then
  now=$(date +%s)
  last=$(cat "$STAMP_FILE" 2>/dev/null || echo 0)
  [[ "$last" =~ ^[0-9]+$ ]] || last=0
  if [ $((now - last)) -ge "$WARN_INTERVAL_SEC" ]; then
    log "WARN: 空き ${avail_gb}GB（${WARN_GB}GB 未満）→ 通知"
    notify "kiseki ディスク空き不足" "空き ${avail_gb}GB（${WARN_GB}GB 未満）。不要なファイルを消してください" "Funk"
    echo "$now" > "$STAMP_FILE"
  else
    log "WARN: 空き ${avail_gb}GB（${WARN_GB}GB 未満）・通知は間引き中"
  fi
else
  log "OK: 空き ${avail_gb}GB"
  rm -f "$STAMP_FILE"
fi
