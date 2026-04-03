"""指数算出・CSV出力スクリプト

指定日のレース全馬について全指数を算出し、
calculated_indices テーブルへ保存した上で CSV ファイルを出力する。

使い方:
  # Docker コンテナ内
  uv run python scripts/calculate_indices.py --date 20260322

  # ローカル（.env 読み込み）
  python scripts/calculate_indices.py --date 20260322 --output /tmp/indices_20260322.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import sys
from pathlib import Path

# プロジェクトルートを sys.path へ追加（Docker 外からの直接実行対応）
_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

from sqlalchemy import select

from src.db.models import Horse, RaceEntry
from src.db.session import AsyncSessionLocal
from src.indices.composite import CompositeIndexCalculator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("calculate_indices")


async def run(date: str, output_path: str | None) -> None:
    """指定日の指数を算出して保存・CSV出力する。

    Args:
        date: "YYYYMMDD" 形式の日付
        output_path: CSV 出力先パス。None の場合は stdout へ出力。
    """
    async with AsyncSessionLocal() as db:
        calc = CompositeIndexCalculator(db)
        logger.info(f"算出開始: date={date}")
        rows = await calc.calculate_batch_for_date(date)
        await db.commit()
        logger.info(f"DB保存完了: {len(rows)} 件")

        if not rows:
            logger.warning("算出結果なし。日付・データを確認してください。")
            return

        # 馬名を付与
        horse_ids = list({r["horse_id"] for r in rows})
        horses = (
            await db.execute(select(Horse).where(Horse.id.in_(horse_ids)))
        ).scalars().all()
        horse_name_map = {h.id: h.name for h in horses}

        # 馬番を付与（race_id + horse_id → horse_number）
        entry_map: dict[tuple[int, int], int] = {}
        race_ids = list({r["race_id"] for r in rows})
        entries = (
            await db.execute(select(RaceEntry).where(RaceEntry.race_id.in_(race_ids)))
        ).scalars().all()
        for e in entries:
            entry_map[(e.race_id, e.horse_id)] = e.horse_number

        # CSV 出力
        fieldnames = [
            "date",
            "course_name",
            "race_number",
            "race_name",
            "horse_number",
            "horse_name",
            "composite_index",
            "speed_index",
            "course_aptitude",
            "position_advantage",
            "rotation_index",
            "jockey_index",
            "pace_index",
        ]

        def _sorted_rows(data: list[dict]) -> list[dict]:
            return sorted(
                data,
                key=lambda r: (r["race_number"], entry_map.get((r["race_id"], r["horse_id"]), 99)),
            )

        def _make_row(r: dict) -> dict:
            return {
                "date": r["date"],
                "course_name": r["course_name"],
                "race_number": r["race_number"],
                "race_name": r.get("race_name") or "",
                "horse_number": entry_map.get((r["race_id"], r["horse_id"]), ""),
                "horse_name": horse_name_map.get(r["horse_id"], ""),
                "composite_index": r["composite_index"],
                "speed_index": r["speed_index"],
                "course_aptitude": r["course_aptitude"],
                "position_advantage": r["position_advantage"],
                "rotation_index": r["rotation_index"],
                "jockey_index": r["jockey_index"],
                "pace_index": r["pace_index"],
            }

        sorted_data = [_make_row(r) for r in _sorted_rows(rows)]

        if output_path:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(sorted_data)
            logger.info(f"CSV出力完了: {path} ({len(sorted_data)} 行)")
        else:
            # stdout へ出力
            writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(sorted_data)

        # サマリー表示
        _print_summary(sorted_data)


def _print_summary(rows: list[dict]) -> None:
    """算出結果のサマリーを表示する。"""
    if not rows:
        return

    print("\n" + "=" * 70)
    print(f"  指数算出サマリー  {rows[0]['date']}")
    print("=" * 70)

    current_race = None
    for r in rows:
        race_key = (r["race_number"], r["race_name"])
        if race_key != current_race:
            current_race = race_key
            print(f"\n  R{r['race_number']:>2} {r['course_name']} {r['race_name'] or ''}")
            print(
                f"  {'馬番':>4} {'馬名':>18} {'総合':>6} {'速度':>6} {'コース':>6} {'展開':>6} {'騎手':>6} {'ロテ':>6}"
            )
            print("  " + "-" * 62)

        print(
            f"  {r['horse_number']:>4} {str(r['horse_name']):>18} "
            f"{r['composite_index']:>6.1f} {r['speed_index']:>6.1f} "
            f"{r['course_aptitude']:>6.1f} {r['pace_index']:>6.1f} "
            f"{r['jockey_index']:>6.1f} {r['rotation_index']:>6.1f}"
        )

    print("=" * 70)


def main() -> None:
    """エントリーポイント。"""
    parser = argparse.ArgumentParser(description="指数算出・CSV出力スクリプト")
    parser.add_argument(
        "--date",
        required=True,
        help="対象日付 (YYYYMMDD)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="CSV出力先パス。省略時は stdout へ出力。",
    )
    args = parser.parse_args()

    if len(args.date) != 8 or not args.date.isdigit():
        parser.error("--date は YYYYMMDD 形式で指定してください (例: 20260322)")

    asyncio.run(run(args.date, args.output))


if __name__ == "__main__":
    main()
