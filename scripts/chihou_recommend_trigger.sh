#!/bin/bash
# 【DEPRECATED】 地方競馬推奨生成トリガー
#
# 2026-04-28 以降、地方推奨生成は Claude.ai Routine に移行済み（毎朝09:00 JST）。
# このスクリプトは VPS cron から呼ばれていたが、cron 設定を停止すること。
#
# 停止手順（VPS 上で実行）:
#   crontab -e
#   下記の行をコメントアウト:
#     0 1 * * * /home/ysuzuki/GitHub/kiseki/scripts/chihou_recommend_trigger.sh
#
# 結果更新（chihou_results_trigger.sh）と オッズ判断更新（chihou_odds_decision_trigger.sh）
# は維持する（Claude API 不使用）。

echo "[DEPRECATED] chihou_recommend_trigger.sh は廃止されました。" >&2
echo "推奨生成は Claude.ai Routine に移行済み（POST /api/chihou/recommendations/submit）。" >&2
echo "VPS cron から本スクリプトの呼び出しを除去してください。" >&2
exit 2
