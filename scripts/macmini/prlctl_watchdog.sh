#!/bin/bash
# 90 秒以上ハングした `prlctl exec` を kill する（launchd から 30 秒毎）
#
# prlctl exec がハングすると Parallels Tools Service が約 7 秒毎にリトライし、
# Windows 側にウィンドウが出続ける（CLAUDE.md「prlctl exec のウィンドウちらつき」）。
# MacBook では plist にインラインで書かれていてリポジトリに無かったので、ここに置く。
set -uo pipefail

LIMIT="${PRLCTL_WATCHDOG_LIMIT:-90}"

# macOS の ps に etimes は無い。etime の [[dd-]hh:]mm:ss を秒に直す
ps -Ao pid=,etime=,command= | awk -v limit="$LIMIT" '
  $3 ~ /prlctl$/ && $4 == "exec" {
    t = $2; d = 0
    if (index(t, "-")) { split(t, a, "-"); d = a[1]; t = a[2] }
    n = split(t, p, ":")
    s = (n == 3) ? p[1]*3600 + p[2]*60 + p[3] : p[1]*60 + p[2]
    s += d * 86400
    if (s > limit) print $1, s
  }' | while read -r pid secs; do
  echo "$(date '+%Y-%m-%d %H:%M:%S') kill prlctl exec pid=$pid (${secs}s)"
  kill -9 "$pid" 2>/dev/null || true
done
exit 0
