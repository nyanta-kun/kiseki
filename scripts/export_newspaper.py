"""出馬表+指数 確認スクリプト

DBから指定日の出馬表と算出済み指数を取得してコンソール表示する。
CSV出力が必要な場合は --csv オプションを指定。

使い方:
  uv run python scripts/export_newspaper.py --date 20260322
  uv run python scripts/export_newspaper.py --date 20260322 --course 05
  uv run python scripts/export_newspaper.py --date 20260322 --csv output.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from src.config import settings  # noqa: E402
from src.db.models import CalculatedIndex, Horse, Jockey, Race, RaceEntry  # noqa: E402
from src.db.session import SessionLocal  # noqa: E402


def fetch_newspaper_rows(date: str, course: str | None, db) -> list[dict]:
    """出馬表+指数をDBから取得する。"""
    q = (
        db.query(Race, RaceEntry, Horse, Jockey, CalculatedIndex)
        .join(RaceEntry, RaceEntry.race_id == Race.id)
        .join(Horse, RaceEntry.horse_id == Horse.id)
        .outerjoin(Jockey, RaceEntry.jockey_id == Jockey.id)
        .outerjoin(
            CalculatedIndex,
            (CalculatedIndex.race_id == Race.id)
            & (CalculatedIndex.horse_id == Horse.id),
        )
        .filter(Race.date == date)
    )
    if course:
        q = q.filter(Race.course == course)

    q = q.order_by(Race.race_number, RaceEntry.horse_number)

    rows = []
    for race, entry, horse, jockey, idx in q.all():
        rows.append({
            "日付": race.date,
            "場": race.course_name,
            "R": race.race_number,
            "芝ダ": race.surface,
            "距離": race.distance,
            "状態": race.condition or "",
            "枠": entry.frame_number,
            "馬番": entry.horse_number,
            "馬名": horse.name,
            "性": horse.sex,
            "斤量": float(entry.weight_carried) if entry.weight_carried else "",
            "騎手": jockey.name if jockey else "",
            "馬体重": entry.horse_weight or "",
            "増減": entry.weight_change or "",
            "SP指数": float(idx.speed_index) if idx and idx.speed_index else "",
            "上がり指数": float(idx.last_3f_index) if idx and idx.last_3f_index else "",
            "総合指数": float(idx.composite_index) if idx and idx.composite_index else "",
            "推定勝率": float(idx.win_probability) if idx and idx.win_probability else "",
        })
    return rows


def main():
    """エントリポイント。"""
    parser = argparse.ArgumentParser(description="出馬表+指数表示/CSV出力")
    parser.add_argument("--date", required=True, help="対象日付 YYYYMMDD")
    parser.add_argument("--course", help="場コード (01-10)")
    parser.add_argument("--csv", dest="csv_path", help="CSV出力ファイルパス")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        rows = fetch_newspaper_rows(args.date, args.course, db)
    finally:
        db.close()

    if not rows:
        print(f"データなし: date={args.date}, course={args.course}")
        return

    if args.csv_path:
        with open(args.csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"CSV出力: {args.csv_path} ({len(rows)}行)")
    else:
        headers = list(rows[0].keys())
        col_w = {h: max(len(h), max(len(str(r[h])) for r in rows)) for h in headers}
        header_line = "  ".join(h.ljust(col_w[h]) for h in headers)
        print(header_line)
        print("-" * len(header_line))
        for row in rows:
            print("  ".join(str(row[h]).ljust(col_w[h]) for h in headers))

    print(f"\n合計: {len(rows)}頭")


if __name__ == "__main__":
    main()
