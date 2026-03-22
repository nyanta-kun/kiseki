"""
パーサーバグによる不正データのクリアスクリプト

対象テーブル（keibaスキーマ）:
  - calculated_indices : 不正データから算出された指数
  - race_results       : タイム・着順・通過順が誤り
  - race_entries       : 斤量・枠番・馬番が誤り
  - races              : 開催日・距離・馬場・グレードが誤り
  - horses             : SJIS文字化けにより馬名が誤り
  - jockeys            : SJIS文字化けにより騎手名が誤り
  - trainers           : SJIS文字化けにより調教師名が誤り

使い方:
  cd backend
  .venv/bin/python ../scripts/clear_imported_data.py
"""

import sys
from pathlib import Path

# backendディレクトリをパスに追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from src.config import settings
from src.db.session import engine

SCHEMA = "keiba"

# 外部キー制約の順序で TRUNCATE（参照される側は後に）
TABLES_IN_ORDER = [
    f"{SCHEMA}.calculated_indices",
    f"{SCHEMA}.entry_changes",
    f"{SCHEMA}.odds_history",
    f"{SCHEMA}.race_results",
    f"{SCHEMA}.race_entries",
    f"{SCHEMA}.races",
    f"{SCHEMA}.pedigrees",
    f"{SCHEMA}.horses",
    f"{SCHEMA}.jockeys",
    f"{SCHEMA}.trainers",
]


def main() -> None:
    print("=" * 60)
    print("kiseki - インポートデータ クリアスクリプト")
    print("=" * 60)
    print()
    print("以下のテーブルをクリアします（RESTART IDENTITY）:")
    for t in TABLES_IN_ORDER:
        print(f"  - {t}")
    print()

    answer = input("実行しますか？ [yes/no]: ").strip().lower()
    if answer != "yes":
        print("キャンセルしました。")
        sys.exit(0)

    from sqlalchemy import text

    with engine.begin() as conn:
        for table in TABLES_IN_ORDER:
            conn.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
            print(f"  TRUNCATE {table} ... OK")

    print()
    print("完了しました。全テーブルをクリアしました。")
    print("Windowsで python jvlink_agent.py --mode setup を実行してデータを再取得してください。")


if __name__ == "__main__":
    main()
