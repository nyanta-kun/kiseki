"""【DEPRECATED】 推奨生成スクリプト

2026-04-28 以降、推奨生成は Claude.ai 定期エージェント（Routine）に移行済み。
このスクリプトはもう使用されない。

新しい流れ:
    Claude Routine（毎朝08:00 JST）が
      1. GET /api/recommendations/source?date=YYYYMMDD でソース取得
      2. プロンプトに従い推奨5件を選定
      3. POST /api/recommendations/submit?date=YYYYMMDD でDBに保存

結果更新は scripts/update_recommendation_results.py を引き続き使用する。
"""

import sys


def main() -> None:
    """エラー終了する（誤って cron から呼ばれた場合の保険）。"""
    sys.stderr.write(
        "[DEPRECATED] calculate_recommendations.py は廃止されました。\n"
        "推奨生成は Claude.ai Routine に移行済み（POST /api/recommendations/submit）。\n"
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
