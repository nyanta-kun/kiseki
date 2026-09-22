# Mac mini の launchd ジョブ共通の環境。run_job.sh が source する。
#
# launchd は PATH が /usr/bin:/bin:/usr/sbin:/sbin しか無い。2026-03〜07 に
# realtime_start.sh が「psql が見つからない」を 2>/dev/null で握り潰して毎日
# 「開催なし」と誤報していたのはこれが原因だった。ここで明示する。

export HOME="${HOME:-/Users/ysuzuki}"
export PATH="/opt/homebrew/bin:/opt/homebrew/opt/postgresql@16/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export LANG="${LANG:-ja_JP.UTF-8}"

KISEKI_ROOT="$HOME/GitHub/kiseki"
# /tmp は再起動で消えるので使わない
JOB_LOG_DIR="$KISEKI_ROOT/logs/jobs"
JOB_STATE_DIR="$HOME/.local/state/jobs"

# 秘密情報（KEIRIN_DB_URL / DISCORD_WEBHOOK_URL_SYSTEM 等）はここに集約する。
# crontab や plist に平文で書かない。
KISEKI_SECRETS="$HOME/.config/kiseki/env"
if [ -f "$KISEKI_SECRETS" ]; then
  _perm="$(stat -f %Lp "$KISEKI_SECRETS")"
  if [ "$_perm" != "600" ]; then
    echo "ERROR: $KISEKI_SECRETS の権限が $_perm です（600 にすること）" >&2
    return 1
  fi
  set -a
  # shellcheck disable=SC1090
  . "$KISEKI_SECRETS"
  set +a
fi
