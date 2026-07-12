"""穴ぐさ × 指数上位 Discord 通知スクリプト

当日の穴ぐさA/B × v26指数2位以内の馬を検索し、いれば Discord に通知する。

使い方:
    .venv/bin/python scripts/anagusa_discord_notify.py
    .venv/bin/python scripts/anagusa_discord_notify.py --date 20260628
    .venv/bin/python scripts/anagusa_discord_notify.py --dry-run  # 送信せず標準出力のみ
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

try:
    from dotenv import load_dotenv
    load_dotenv(_root.parent / ".env")
except ImportError:
    pass  # Dockerコンテナ内では環境変数がenv_fileで注入済み

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.db.session import sync_engine as engine
from src.indices.composite import COMPOSITE_VERSION
from src.utils.discord import send

COURSE_MAP_SQL = """
  CASE a.course_code
    WHEN 'JSPK' THEN '01' WHEN 'JHKD' THEN '02' WHEN 'JFKS' THEN '03'
    WHEN 'JNGT' THEN '04' WHEN 'JTOK' THEN '05' WHEN 'JNKY' THEN '06'
    WHEN 'JCKO' THEN '07' WHEN 'JKYO' THEN '08' WHEN 'JHSN' THEN '09'
    WHEN 'JKKR' THEN '10'
  END
"""

# 穴ぐさA/B × 指数順位 2位以内（3年検証: 複勝率32%, 複勝ROI 1.048, 単勝ROI 1.31）
QUERY = text(f"""
WITH race_versions AS (
  -- API (races.py get_indices) と同じレース単位 capped 方式:
  -- レースの最新 version を本番 COMPOSITE_VERSION で上限キャップする
  -- （グローバル MAX だと新バージョンのバックフィル進行中に未計算レースが通知から漏れる）
  SELECT ci.race_id, LEAST(MAX(ci.version), :composite_version) AS use_version
  FROM keiba.calculated_indices ci
  JOIN keiba.races r2 ON r2.id = ci.race_id AND r2.date = :race_date_str
  GROUP BY ci.race_id
),
race_ranks AS (
  SELECT
    ci.race_id,
    ci.horse_id,
    ci.composite_index,
    RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.composite_index DESC) AS idx_rank
  FROM keiba.calculated_indices ci
  JOIN race_versions rv ON rv.race_id = ci.race_id AND ci.version = rv.use_version
)
SELECT
    r.id              AS race_id,
    r.date            AS race_date,
    r.course_name,
    r.race_number,
    r.surface,
    r.distance,
    r.head_count,
    re.horse_number,
    a.horse_name,
    a.rank            AS anagusa_rank,
    rk.idx_rank,
    rk.composite_index,
    oh.odds           AS win_odds
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
JOIN race_ranks rk
    ON rk.race_id = r.id
   AND rk.horse_id = re.horse_id
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
  AND a.rank IN ('A', 'B')
  AND rk.idx_rank <= 2
  AND COALESCE(r.head_count, 8) >= 8
ORDER BY r.course, r.race_number, rk.idx_rank
""")

SURFACE_LABEL = {"turf": "芝", "dirt": "ダ", "obstacle": "障"}


def fetch_picks(target_date: str) -> list[dict]:
    race_date_str = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:8]}"
    with Session(engine) as db:
        rows = db.execute(QUERY, {
            "race_date": race_date_str,
            "race_date_str": target_date,
            "composite_version": COMPOSITE_VERSION,
        }).fetchall()
    return [row._asdict() for row in rows]


def build_message(picks: list[dict], target_date: str) -> str:
    date_label = f"{target_date[:4]}/{target_date[4:6]}/{target_date[6:]}"
    lines = [f"🏇 **穴ぐさ × 指数上位ピック ({date_label})**"]
    lines.append("穴ぐさA/B かつ v26指数2位以内（複勝ROI 1.05 実証済）")
    lines.append("```")
    lines.append(f"{'場':5} {'R':3} {'馬':>2} {'馬名':<12} {'穴':3} {'指数順':4} {'単勝':>6}")
    lines.append("-" * 46)

    for p in picks:
        surf = SURFACE_LABEL.get(p["surface"] or "", "")
        dist = p["distance"] or ""
        odds_str = f"{p['win_odds']:.1f}倍" if p["win_odds"] else " —"
        lines.append(
            f"{p['course_name'][:4]:5}"
            f"{p['race_number']:2}R "
            f"{p['horse_number']:>2}番 "
            f"{str(p['horse_name'])[:10]:<12}"
            f"{p['anagusa_rank']:3}"
            f"{p['idx_rank']:4}位 "
            f"{odds_str:>7}"
        )

    lines.append("```")
    lines.append(f"計 {len(picks)} 頭")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    # コンテナはUTCのため date.today() だと JST 朝の実行時に前日になる。JST固定で当日を求める
    parser.add_argument(
        "--date",
        default=datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y%m%d"),
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Discord送信せず標準出力のみ")
    args = parser.parse_args()

    picks = fetch_picks(args.date)

    if not picks:
        print(f"[anagusa_notify] 対象なし: {args.date} (穴ぐさA/B × 指数2位以内 0頭)")
        return

    message = build_message(picks, args.date)
    print(message)

    if args.dry_run:
        print("[anagusa_notify] --dry-run のため Discord 送信をスキップ")
        return

    ok = send(message)
    if ok:
        print(f"[anagusa_notify] Discord 送信完了 ({len(picks)} 頭)")
    else:
        print("[anagusa_notify] Discord 送信失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
