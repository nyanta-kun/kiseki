#!/usr/bin/env bash
# 型ラボの夜間レビュー（2026-08-29 新設）。**VPS cron 00:10 JST**。
#
# 🔴 **00:10 に走るので対象は「前日」**。`date +%F` は日付が変わったあとの
#    "今日" を指すので、そのまま渡すと**まだ1レースも終わっていない日**を
#    レビューしてしまう（毎晩「入稿0件・未採点」で異常が出続ける）。
#
# 🔴 **23:50 ではなく 00:10 にしてある。** 型ラボの採点 cron は
#    `5,20,35,50 8-23,0`。ミッドナイトの最終レース（23:20〜23:30 発走）の
#    確定着順が入るのは日付が変わったあとで、23:50 だと採点と同時刻に走って
#    その日の最後の数レースを未採点のまま集計してしまう。
#
# 🔴 **Discord へは1行の要約とリンクだけ**（2026-08-30 変更・ユーザー要望）。
#    本文は図表つき HTML で配る。長文を貼ると読まれない。
#
# 何度流しても害はない（台帳は同じ日を上書きする）。
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${KEIRIN_PYTHON:-.venv/bin/python3}"
DAY="${1:-$(date -d '1 day ago' +%F 2>/dev/null || date -v-1d +%F)}"

# 配信先。**トークンは推測不可な公開URLの唯一の防御**なので、cron の環境変数か
# .env で渡し、リポジトリには置かない。
NIGHTLY_DIR="${KEIRIN_NIGHTLY_DIR:-}"
NIGHTLY_URL="${KEIRIN_NIGHTLY_URL:-}"
if [ -z "$NIGHTLY_DIR" ] && [ -f .env ]; then
  NIGHTLY_DIR="$(grep -E '^KEIRIN_NIGHTLY_DIR=' .env | head -1 | cut -d= -f2-)"
  NIGHTLY_URL="$(grep -E '^KEIRIN_NIGHTLY_URL=' .env | head -1 | cut -d= -f2-)"
fi

# ① Markdown（台帳への追記もここで行う）。Discord へは送らない。
PYTHONPATH=. "$PY" scripts/nightly_review_type_lab.py "$DAY" --no-discord

# ② 図表つき HTML と、Discord 用の1行要約。
SUM="data/analysis/nightly/$DAY.summary.txt"
TRI="data/analysis/nightly/$DAY.triage.md"
PYTHONPATH=. "$PY" scripts/nightly_report_html.py "$DAY" \
  --out "data/analysis/nightly/$DAY.html" --summary-out "$SUM" \
  $([ -f "$TRI" ] && echo "--triage $TRI" || true)

# ③ 配信（nginx が読める場所へ置く。/home は 0750 で辿れない）。
if [ -n "$NIGHTLY_DIR" ]; then
  mkdir -p "$NIGHTLY_DIR"
  cp "data/analysis/nightly/$DAY.html" "$NIGHTLY_DIR/$DAY.html"
fi

# ④ Discord は1行＋リンクだけ。
if [ -n "$NIGHTLY_URL" ]; then
  MSG="$(cat "$SUM")
📊 <$NIGHTLY_URL/$DAY.html>"
  printf '%s' "$MSG" | "$PY" -c "import sys; sys.path.insert(0, '.'); \
from src.notify.discord import send; \
sys.exit(0 if send(sys.stdin.read(), channel='results') else 1)" \
    || echo "[nightly_review] ⚠️ Discord への送信に失敗"
else
  echo "[nightly_review] KEIRIN_NIGHTLY_URL 未設定のため Discord へは送らない"
fi
