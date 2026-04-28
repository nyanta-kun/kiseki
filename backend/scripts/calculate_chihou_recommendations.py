"""【DEPRECATED】 地方推奨生成スクリプト

2026-04-28 以降、地方推奨生成は Claude.ai 定期エージェント（Routine）に移行済み。
このスクリプトはもう使用されない。

新しい流れ:
    Claude Routine（毎朝09:00 JST）が
      1. GET /api/chihou/recommendations/source?date=YYYYMMDD でソース取得
      2. プロンプトに従い推奨5件を選定
      3. POST /api/chihou/recommendations/submit?date=YYYYMMDD でDBに保存

結果更新は scripts/update_chihou_recommendation_results.py を引き続き使用する。
オッズ判断更新は POST /api/chihou/recommendations/update-odds-decision を毎分cronで継続。
"""

import sys


def main() -> None:
    """エラー終了する（誤って cron から呼ばれた場合の保険）。"""
    sys.stderr.write(
        "[DEPRECATED] calculate_chihou_recommendations.py は廃止されました。\n"
        "推奨生成は Claude.ai Routine に移行済み（POST /api/chihou/recommendations/submit）。\n"
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
