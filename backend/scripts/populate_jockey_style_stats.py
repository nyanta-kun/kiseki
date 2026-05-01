"""騎手戦法統計を集計して keiba.jockey_running_style_stats に保存する。

直近 N ヶ月（デフォルト 24ヶ月）の race_results から、各騎手の
脚質割合（逃げ・先行・中団・後方・マクリ）と戦法多様性を計算する。

脚質判定は relative_pos = passing_4 / head_count を使い、
pace.py / pace_handicap.py と同じ閾値を採用する:
  - escape : < 0.10 （※IMG_9218より逃げ率は厳しめ）
  - leader : < 0.30
  - mid    : < 0.65
  - closer : >= 0.65
マクリ判定: passing_1 - passing_4 >= 5（4Cで5人以上順位上昇）

使い方:
  python scripts/populate_jockey_style_stats.py
  python scripts/populate_jockey_style_stats.py --window 24 --min-rides 10

月次更新を想定。古いレコードは window_months 単位で UPSERT。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import os
import psycopg2

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("populate_jockey_style")

# 脚質分類閾値（passing_4 / head_count）
ESCAPE_THRESHOLD = 0.10
LEADER_THRESHOLD = 0.30
MID_THRESHOLD = 0.65
# マクリ閾値（passing_1 - passing_4 が +5以上）
MAKURI_DELTA = 5

AGGREGATE_QUERY = """
WITH jockey_race AS (
  SELECT
    rr.jockey_id,
    rr.passing_1,
    rr.passing_4,
    r.head_count,
    -- relative_pos = 4C通過 / 頭数
    CASE
      WHEN rr.passing_4 IS NULL OR r.head_count IS NULL OR r.head_count <= 0 THEN NULL
      ELSE rr.passing_4::numeric / r.head_count
    END AS rel_pos,
    -- マクリ判定: 1C→4Cで MAKURI_DELTA 人以上順位を上げた
    CASE
      WHEN rr.passing_1 IS NULL OR rr.passing_4 IS NULL THEN 0
      WHEN (rr.passing_1 - rr.passing_4) >= %(makuri_delta)s THEN 1
      ELSE 0
    END AS is_makuri
  FROM keiba.race_results rr
  JOIN keiba.races r ON r.id = rr.race_id
  WHERE r.date >= %(since)s
    AND COALESCE(rr.abnormality_code, 0) = 0
    AND rr.jockey_id IS NOT NULL
)
SELECT
  jockey_id,
  COUNT(*) AS total_rides,
  AVG(CASE WHEN rel_pos < %(escape_th)s THEN 1.0 ELSE 0.0 END)                            AS escape_rate,
  AVG(CASE WHEN rel_pos >= %(escape_th)s AND rel_pos < %(leader_th)s THEN 1.0 ELSE 0.0 END) AS leader_rate,
  AVG(CASE WHEN rel_pos >= %(leader_th)s AND rel_pos < %(mid_th)s    THEN 1.0 ELSE 0.0 END) AS mid_rate,
  AVG(CASE WHEN rel_pos >= %(mid_th)s                                THEN 1.0 ELSE 0.0 END) AS closer_rate,
  AVG(is_makuri::numeric)                                                                  AS makuri_rate
FROM jockey_race
WHERE rel_pos IS NOT NULL
GROUP BY jockey_id
HAVING COUNT(*) >= %(min_rides)s
"""

UPSERT_QUERY = """
INSERT INTO keiba.jockey_running_style_stats
  (jockey_id, window_months, total_rides,
   escape_rate, leader_rate, mid_rate, closer_rate, makuri_rate, diversity, calculated_at)
VALUES
  (%(jockey_id)s, %(window)s, %(total_rides)s,
   %(escape_rate)s, %(leader_rate)s, %(mid_rate)s, %(closer_rate)s, %(makuri_rate)s, %(diversity)s, NOW())
ON CONFLICT (jockey_id, window_months) DO UPDATE SET
  total_rides   = EXCLUDED.total_rides,
  escape_rate   = EXCLUDED.escape_rate,
  leader_rate   = EXCLUDED.leader_rate,
  mid_rate      = EXCLUDED.mid_rate,
  closer_rate   = EXCLUDED.closer_rate,
  makuri_rate   = EXCLUDED.makuri_rate,
  diversity     = EXCLUDED.diversity,
  calculated_at = NOW();
"""


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--window", type=int, default=24, help="集計対象の月数（デフォルト24）")
    p.add_argument("--min-rides", type=int, default=10, help="最小騎乗数（これ未満の騎手は集計しない）")
    args = p.parse_args()

    # 開始日 = 今日 - window ヶ月
    today = datetime.now()
    months_back = args.window
    year_back = months_back // 12
    month_back = months_back % 12
    new_year = today.year - year_back
    new_month = today.month - month_back
    if new_month <= 0:
        new_year -= 1
        new_month += 12
    since = f"{new_year:04d}{new_month:02d}{today.day:02d}"

    logger.info(f"集計開始: window={args.window}ヶ月, since={since}, min_rides={args.min_rides}")

    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()

    cur.execute(
        AGGREGATE_QUERY,
        {
            "since": since,
            "min_rides": args.min_rides,
            "escape_th": ESCAPE_THRESHOLD,
            "leader_th": LEADER_THRESHOLD,
            "mid_th": MID_THRESHOLD,
            "makuri_delta": MAKURI_DELTA,
        },
    )
    rows = cur.fetchall()
    logger.info(f"対象騎手: {len(rows)}人")

    saved = 0
    for jockey_id, total_rides, esc, lead, mid, closer, makuri in rows:
        # 戦法多様性 = 1 - Σpᵢ²（高い=柔軟、低い=特化）
        rates = [float(r) for r in (esc, lead, mid, closer)]
        diversity = 1.0 - sum(p * p for p in rates)

        cur.execute(
            UPSERT_QUERY,
            {
                "jockey_id": jockey_id,
                "window": args.window,
                "total_rides": total_rides,
                "escape_rate": float(esc),
                "leader_rate": float(lead),
                "mid_rate": float(mid),
                "closer_rate": float(closer),
                "makuri_rate": float(makuri),
                "diversity": round(diversity, 3),
            },
        )
        saved += 1

    conn.commit()
    cur.close()
    conn.close()
    logger.info(f"完了: {saved} 騎手分の戦法統計を保存")


if __name__ == "__main__":
    main()
