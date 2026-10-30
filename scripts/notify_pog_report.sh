#!/bin/bash
# POG 週報「赤兎週報」を作って Discord へ PDF 添付で送る（Mac mini の launchd から週次）
#
# 中身は backend/scripts/notify_pog_report.py。
# 2026-09-16 に Claude Desktop から手動で 1 回だけ送った「赤兎日報」（Windows 用の
# pog_daily セット・未設置のまま）を kiseki へ移したもの。週 1 回なので週報にした。
#
# 🔴 なぜ VPS ではなく Mac mini か
#   PDF 化に Chrome（ヘッドレス印刷）と日本語フォントが要る。backend コンテナには
#   どちらも無い。Mac mini は netkeiba を叩くジョブ（scrape-projected）も既に回している。
#
# 🔴 なぜ水曜 20:30 か
#   19:00 scrape-projected（Mac mini）が netkeiba の出走想定を keiba.projected_entries へ入れ、
#   19:30 に出走想定の通知（VPS）が出る。その後に送る。
#   netkeiba は 70 頭 × 2 リクエスト × 約8秒 ≒ 20 分かかる。
#
# 手動実行:
#   scripts/notify_pog_report.sh --dry-run --out /tmp/pog.pdf   # 送らずに PDF だけ
#   scripts/notify_pog_report.sh --force                        # 送信済みでも送り直す
#
# 終了コード: 0 = 正常（送るグループが無いのも正常）。1 = 送信か PDF 化に失敗した。

set -u

PROJECT_ROOT="${KISEKI_ROOT:-/Users/ysuzuki/GitHub/kiseki}"
BACKEND_DIR="$PROJECT_ROOT/backend"
PYTHON="$BACKEND_DIR/.venv/bin/python"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1"; }

log "=== notify_pog_report.sh 開始 ($*) ==="
cd "$BACKEND_DIR" || exit 1
"$PYTHON" scripts/notify_pog_report.py "$@"
RC=$?
[ "$RC" -eq 0 ] && log "完了" || log "異常終了: rc=$RC"
exit "$RC"
