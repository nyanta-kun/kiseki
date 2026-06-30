"""夏穴 Discord 通知スクリプト

馬体重発表後に夏穴バッジ条件を満たす馬を検索し、いれば Discord に通知する。

条件（backtest_summer_lightweight.py 検証済み、単ROI 2.133, n=539）:
  - 夏競馬場: 6-9月 × 場コード 01/02/03/04/07/10
  - 芝
  - 牡・セン
  - 馬体重 ≤ 470kg
  - 前走比 -4〜-6kg
  - 7番人気以上（オッズから推定）

使い方:
    .venv/bin/python scripts/natsu_ana_discord_notify.py
    .venv/bin/python scripts/natsu_ana_discord_notify.py --date 20260706
    .venv/bin/python scripts/natsu_ana_discord_notify.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

try:
    from dotenv import load_dotenv
    load_dotenv(_root.parent / ".env")
except ImportError:
    pass

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.db.session import sync_engine as engine
from src.utils.discord import send

# 夏競馬場コード（非東京・中山・京都・阪神）
SUMMER_COURSES = ("01", "02", "03", "04", "07", "10")
COURSE_NAMES = {
    "01": "札幌", "02": "函館", "03": "福島",
    "04": "新潟", "07": "中京", "10": "小倉",
}

QUERY = text("""
WITH odds_ranked AS (
  SELECT
    oh.race_id,
    oh.combination AS horse_number_str,
    oh.odds AS win_odds,
    RANK() OVER (
      PARTITION BY oh.race_id
      ORDER BY oh.odds ASC NULLS LAST
    ) AS pop_rank
  FROM keiba.odds_history oh
  WHERE oh.bet_type = 'win'
    AND oh.fetched_at = (
      SELECT MAX(fetched_at)
      FROM keiba.odds_history oh2
      WHERE oh2.race_id = oh.race_id AND oh2.bet_type = 'win'
    )
),
latest_odds AS (
  SELECT DISTINCT ON (race_id, horse_number_str)
    race_id, horse_number_str, win_odds, pop_rank
  FROM odds_ranked
),
race_ranks AS (
  SELECT
    ci.race_id,
    ci.horse_id,
    ci.composite_index,
    RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.composite_index DESC) AS idx_rank
  FROM keiba.calculated_indices ci
  WHERE ci.version = (SELECT MAX(version) FROM keiba.calculated_indices)
)
SELECT
    r.id              AS race_id,
    r.date            AS race_date,
    r.course_name,
    r.race_number,
    r.surface,
    r.distance,
    r.post_time,
    re.horse_number,
    h.name            AS horse_name,
    h.sex,
    re.horse_weight,
    re.weight_change,
    lo.win_odds,
    lo.pop_rank,
    rr.idx_rank
FROM keiba.race_entries re
JOIN keiba.races r     ON r.id = re.race_id
JOIN keiba.horses h    ON h.id = re.horse_id
LEFT JOIN latest_odds lo
    ON lo.race_id = r.id
   AND lo.horse_number_str = CAST(re.horse_number AS TEXT)
LEFT JOIN race_ranks rr
    ON rr.race_id = r.id
   AND rr.horse_id = re.horse_id
WHERE r.date = :race_date
  AND SUBSTRING(r.date, 5, 2) IN ('06', '07', '08', '09')
  AND r.course IN ('01', '02', '03', '04', '07', '10')
  AND r.surface = '芝'
  AND h.sex IN ('牡', 'セ')
  AND re.horse_weight IS NOT NULL
  AND re.horse_weight <= 470
  AND re.weight_change IS NOT NULL
  AND re.weight_change BETWEEN -6 AND -4
  AND re.horse_number > 0
  AND COALESCE(lo.pop_rank, 99) >= 7
ORDER BY r.course, r.race_number, re.horse_number
""")


def fetch_picks(target_date: str) -> list[dict]:
    with Session(engine) as db:
        rows = db.execute(QUERY, {"race_date": target_date}).fetchall()
    return [row._asdict() for row in rows]


def build_message(picks: list[dict], target_date: str) -> str:
    date_label = f"{target_date[:4]}/{target_date[4:6]}/{target_date[6:]}"
    lines = [f"🌊 **夏穴ピック ({date_label})**"]
    lines.append("牡セン≤470kg × 芝 × 前走比-4〜-6kg × 7番人気以上 (3年単ROI 2.133)")
    lines.append("```")
    lines.append(f"{'場':5} {'R':3} {'馬':>2} {'馬名':<12} {'体重':>5} {'変':>4} {'人気':>3} {'指数順':4} {'単勝':>6}")
    lines.append("-" * 54)

    for p in picks:
        chg = p["weight_change"]
        chg_str = f"{chg:+d}" if chg is not None else "  —"
        pop = p["pop_rank"]
        pop_str = f"{pop}番" if pop is not None else " —"
        odds_str = f"{p['win_odds']:.1f}倍" if p["win_odds"] else "  —"
        idx_str = f"{p['idx_rank']}位" if p["idx_rank"] else " —"
        wt = p["horse_weight"] or 0
        post = p["post_time"] or ""
        time_str = f" ({post[:2]}:{post[2:]})" if len(post) >= 4 else ""
        lines.append(
            f"{p['course_name'][:4]:5}"
            f"{p['race_number']:2}R{time_str}"
            f"{p['horse_number']:>3}番 "
            f"{str(p['horse_name'])[:10]:<12}"
            f"{wt:>4}kg"
            f"{chg_str:>4} "
            f"{pop_str:>4} "
            f"{idx_str:>4} "
            f"{odds_str:>7}"
        )

    lines.append("```")
    lines.append(f"計 {len(picks)} 頭")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--dry-run", action="store_true",
                        help="Discord送信せず標準出力のみ")
    args = parser.parse_args()

    # 夏季以外は静かに終了
    month = args.date[4:6]
    if month not in ("06", "07", "08", "09"):
        print(f"[natsu_ana] 夏季外のためスキップ: {args.date} (月={month})")
        return

    picks = fetch_picks(args.date)

    if not picks:
        print(f"[natsu_ana] 対象なし: {args.date} (夏穴条件 0頭)")
        return

    message = build_message(picks, args.date)
    print(message)

    if args.dry_run:
        print("[natsu_ana] --dry-run のため Discord 送信をスキップ")
        return

    ok = send(message)
    if ok:
        print(f"[natsu_ana] Discord 送信完了 ({len(picks)} 頭)")
    else:
        print("[natsu_ana] Discord 送信失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
