"""穴ぐさ期待度日次レポートスクリプト

当日（または指定日）の穴ぐさピック馬について、バイアス補正済み期待度スコアと
オッズを組み合わせ、期待値の高い馬・レースを抽出して表示する。

使い方:
    python scripts/anagusa_daily.py                    # 本日
    python scripts/anagusa_daily.py --date 20260322   # 指定日

出力:
    rank  馬名              穴ぐさスコア  複勝オッズ  期待値  課題コメント
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date

# SQLAlchemy のエコーログをインポート前に抑制
logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.basicConfig(level=logging.WARNING)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import text

from src.db.session import SessionLocal
from src.indices.anagusa import (
    OVERALL_PLACE_RATE,
    AnagusaIndexCalculator,
)


def main() -> None:
    """穴ぐさ期待度日次レポートを出力する。"""
    parser = argparse.ArgumentParser(description="穴ぐさ期待度日次レポート")
    parser.add_argument(
        "--date",
        default=date.today().strftime("%Y%m%d"),
        help="対象日 (YYYYMMDD, default: today)",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=55.0,
        help="最低穴ぐさスコア (default: 55.0)",
    )
    args = parser.parse_args()

    target_date = args.date
    race_date = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:8]}"

    db = SessionLocal()
    try:
        calc = AnagusaIndexCalculator(db)

        # --- 1. 当日のピックを全件取得 ---
        sql = text(
            """
            SELECT
                a.course_code,
                a.race_no,
                a.horse_no,
                a.horse_name,
                a.rank,
                a.comment,
                r.id        AS race_id,
                r.course_name,
                r.surface,
                r.distance,
                r.head_count,
                re.horse_id,
                -- 最新の確定単勝オッズ (odds_history から)
                oh.odds     AS win_odds
            FROM sekito.anagusa a
            JOIN (
                SELECT unnest(ARRAY['JSPK','JHKD','JFKS','JNGT','JTOK','JNKY','JCKO','JKYO','JHSN','JKKR']) AS sekito_code,
                       unnest(ARRAY['01',  '02',  '03',  '04',  '05',  '06',  '07',  '08',  '09',  '10' ]) AS jra_code
            ) cm ON cm.sekito_code = a.course_code
            JOIN keiba.races r
                ON r.date = :race_date_str
               AND r.course = cm.jra_code
               AND r.race_number = a.race_no
            JOIN keiba.race_entries re
                ON re.race_id = r.id
               AND re.horse_number = a.horse_no
            LEFT JOIN LATERAL (
                SELECT odds
                FROM keiba.odds_history
                WHERE race_id = r.id
                  AND bet_type = 'win'
                  AND combination = CAST(a.horse_no AS TEXT)
                ORDER BY fetched_at DESC
                LIMIT 1
            ) oh ON TRUE
            WHERE a.date = :race_date
            ORDER BY a.course_code, a.race_no, a.rank, a.horse_no
            """
        )

        rows = db.execute(
            sql,
            {
                "race_date": race_date,
                "race_date_str": target_date,
            },
        ).fetchall()

        if not rows:
            print(f"穴ぐさピックなし: {target_date}")
            return

        # --- 2. 各馬のスコアを算出 ---
        # レースごとにキャッシュ
        processed_races: dict[int, dict[int, float]] = {}

        print(f"\n{'=' * 80}")
        print(f"穴ぐさ期待度レポート: {target_date}")
        print(f"{'=' * 80}")
        print(
            f"{'競馬場':<6}{'R':<3}{'rank':<5}{'馬番':<4}{'馬名':<18}{'穴スコア':<9}{'単勝':<8}{'期待値'}"
        )
        print(f"{'-' * 80}")

        course_prev = None
        for row in rows:
            # スコア算出
            if row.race_id not in processed_races:
                processed_races[row.race_id] = calc.calculate_batch(row.race_id)
            anagusa_score = processed_races[row.race_id].get(row.horse_id, 50.0)

            if anagusa_score < args.min_score:
                continue

            # 期待値 = 複勝確率推定 × オッズ
            # 穴ぐさスコアから複勝確率を逆算（簡易近似）
            # score=75 → 19.4%, score=60 → 14.9%, score=42 → 11.8%
            est_place_rate = OVERALL_PLACE_RATE * (anagusa_score / 50.0) / 100.0

            if row.win_odds:
                # 複勝オッズは単勝の1/3程度として近似（実オッズ未取得時）
                place_odds_approx = float(row.win_odds) * 0.35
                expected_value = est_place_rate * place_odds_approx
                ev_str = f"{expected_value:.2f}"
            else:
                ev_str = "—"

            odds_str = f"{row.win_odds:.1f}" if row.win_odds else "—"

            if row.course_code != course_prev:
                if course_prev is not None:
                    print()
                course_prev = row.course_code

            print(
                f"{row.course_name:<6}"
                f"R{row.race_no:<2}"
                f"{row.rank:<5}"
                f"{row.horse_no:<4}"
                f"{row.horse_name:<18}"
                f"{anagusa_score:<9.1f}"
                f"{odds_str:<8}"
                f"{ev_str}"
            )

        print(f"\n{'=' * 80}")
        print("※穴ぐさスコア: ピック実績(2024-2026)ベース + コース/距離/頭数バイアス補正")
        print("  A=75基準, B=60基準, C=42基準, ニュートラル(未ピック)=50")
        print(f"  全体複勝率実績: {OVERALL_PLACE_RATE}%  (A: 19.4%, B: 14.9%, C: 11.8%)")

    finally:
        db.close()


if __name__ == "__main__":
    main()
