"""地方競馬(chihou) データ健全性チェック。

keirin の check_race_point_sanity.py（直近中央値比50%未満で異常検知）を参考に、
chihou のデータパイプラインで「静かに壊れる」ことが多い箇所を日次でチェックする:

  1. 外部指数(kichiuma/netkeiba)供給率 — スクレイプ断続で ext_missing が急増していないか
  2. 単複オッズ充足率 — 確定済みレースで win_odds/place_odds の欠損が急増していないか
  3. calculated_indices 算出漏れ — 出走予定馬に対して指数が算出されていないレースがないか
  4. composite_index 分布の異常 — 特定日だけ極端な分布（定数化・NaN化等）になっていないか

各チェックは「直近N日 vs 直前baseline期間」の比率で判定する。異常があれば標準出力に
WARN を出し、exit code 1 で終了する（cron/LaunchAgent 化して失敗を検知する用途を想定。
Discord通知等への接続は未実装 — 導入する場合は notify_*.py 系と同様の webhook 呼び出しを追加する）。

使い方:
  cd backend
  .venv/bin/python scripts/chihou_data_health_check.py [--recent-days 3] [--baseline-days 30]
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv
load_dotenv(_root.parent / ".env")

import psycopg2
import psycopg2.extras

from src.indices.chihou_calculator import CHIHOU_COMPOSITE_VERSION

DSN = (
    f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
    f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
    f"password={os.getenv('DB_PASSWORD')}"
)

WARN_RATIO = 0.5  # baseline比でこの割合を下回ったらWARN


def _fetchone(cur, sql: str, params: tuple) -> tuple:
    cur.execute(sql, params)
    return cur.fetchone()


def check_external_index_coverage(cur, recent_start: str, end: str, baseline_start: str, baseline_end: str) -> bool:
    """kichiuma/netkeibaの供給率が直近で急落していないか。"""
    sql = """
        SELECT
            COUNT(*) FILTER (WHERE k.sp_score IS NOT NULL OR n.idx_ave IS NOT NULL) AS covered,
            COUNT(*) AS total
        FROM chihou.races r
        JOIN chihou.race_entries re ON re.race_id = r.id
        LEFT JOIN sekito.racecourse rc ON rc.netkeiba_id = r.course
        LEFT JOIN sekito.kichiuma k
          ON k.date = TO_DATE(r.date, 'YYYYMMDD') AND k.course_code = rc.code
             AND k.race_no = r.race_number AND k.horse_no = re.horse_number
        LEFT JOIN sekito.netkeiba n
          ON n.date = TO_DATE(r.date, 'YYYYMMDD') AND n.course_code = rc.code
             AND n.race_no = r.race_number AND n.horse_no = re.horse_number
        WHERE r.course != '83' AND r.date >= %s AND r.date <= %s
    """
    recent_covered, recent_total = _fetchone(cur, sql, (recent_start, end))
    base_covered, base_total = _fetchone(cur, sql, (baseline_start, baseline_end))

    recent_rate = recent_covered / recent_total if recent_total else 0.0
    base_rate = base_covered / base_total if base_total else 0.0
    ratio = recent_rate / base_rate if base_rate else 1.0
    ok = ratio >= WARN_RATIO or base_total == 0
    status = "OK  " if ok else "WARN"
    print(
        f"[{status}] 外部指数供給率: 直近{recent_rate*100:5.1f}% "
        f"(baseline {base_rate*100:5.1f}%, 比率{ratio*100:5.1f}%)  "
        f"recent_n={recent_total:,} baseline_n={base_total:,}"
    )
    return ok


def check_odds_completeness(cur, recent_start: str, end: str, baseline_start: str, baseline_end: str) -> bool:
    """確定済みレースで win_odds/place_odds の欠損率が急増していないか。"""
    sql = """
        SELECT
            COUNT(*) FILTER (WHERE rr.win_odds IS NOT NULL) AS win_ok,
            COUNT(*) FILTER (WHERE rr.place_odds IS NOT NULL) AS place_ok,
            COUNT(*) AS total
        FROM chihou.race_results rr
        JOIN chihou.races r ON r.id = rr.race_id
        WHERE r.course != '83' AND r.date >= %s AND r.date <= %s
          AND rr.finish_position IS NOT NULL
    """
    r_win, r_place, r_total = _fetchone(cur, sql, (recent_start, end))
    b_win, b_place, b_total = _fetchone(cur, sql, (baseline_start, baseline_end))

    ok_all = True
    for label, r_ok, b_ok in [("単勝オッズ", r_win, b_win), ("複勝オッズ", r_place, b_place)]:
        r_rate = r_ok / r_total if r_total else 0.0
        b_rate = b_ok / b_total if b_total else 0.0
        ratio = r_rate / b_rate if b_rate else 1.0
        ok = ratio >= WARN_RATIO or b_total == 0
        ok_all &= ok
        status = "OK  " if ok else "WARN"
        print(
            f"[{status}] {label}充足率: 直近{r_rate*100:5.1f}% "
            f"(baseline {b_rate*100:5.1f}%, 比率{ratio*100:5.1f}%)  "
            f"recent_n={r_total:,} baseline_n={b_total:,}"
        )
    return ok_all


def check_index_calculation_gap(cur, recent_start: str, end: str) -> bool:
    """出走予定馬に対して現行versionの指数が算出されていないレースがないか。"""
    sql = """
        SELECT COUNT(DISTINCT r.id)
        FROM chihou.races r
        JOIN chihou.race_entries re ON re.race_id = r.id
        LEFT JOIN chihou.calculated_indices ci
          ON ci.race_id = r.id AND ci.horse_id = re.horse_id AND ci.version = %s
        WHERE r.course != '83' AND r.date >= %s AND r.date <= %s
          AND ci.race_id IS NULL
    """
    cur.execute(sql, (CHIHOU_COMPOSITE_VERSION, recent_start, end))
    (missing_races,) = cur.fetchone()
    ok = missing_races == 0
    status = "OK  " if ok else "WARN"
    print(f"[{status}] 指数算出漏れ(v{CHIHOU_COMPOSITE_VERSION}): 未算出レース {missing_races:,} 件（直近{recent_start}〜{end}）")
    return ok


def check_composite_index_distribution(cur, recent_start: str, end: str, baseline_start: str, baseline_end: str) -> bool:
    """composite_indexの標準偏差が直近で異常に潰れていないか（定数化バグの検知）。"""
    sql = """
        SELECT STDDEV(ci.composite_index)
        FROM chihou.calculated_indices ci
        JOIN chihou.races r ON r.id = ci.race_id
        WHERE ci.version = %s AND r.course != '83' AND r.date >= %s AND r.date <= %s
    """
    cur.execute(sql, (CHIHOU_COMPOSITE_VERSION, recent_start, end))
    (recent_std,) = cur.fetchone()
    cur.execute(sql, (CHIHOU_COMPOSITE_VERSION, baseline_start, baseline_end))
    (base_std,) = cur.fetchone()

    recent_std = float(recent_std) if recent_std is not None else 0.0
    base_std = float(base_std) if base_std is not None else 0.0
    ratio = recent_std / base_std if base_std else 1.0
    ok = ratio >= WARN_RATIO or base_std == 0.0
    status = "OK  " if ok else "WARN"
    print(
        f"[{status}] composite_index 標準偏差: 直近{recent_std:.2f} "
        f"(baseline {base_std:.2f}, 比率{ratio*100:5.1f}%)"
    )
    return ok


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--recent-days", type=int, default=3)
    p.add_argument("--baseline-days", type=int, default=30)
    args = p.parse_args()

    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    end = today.strftime("%Y%m%d")
    recent_start = (today - timedelta(days=args.recent_days)).strftime("%Y%m%d")
    baseline_start = (today - timedelta(days=args.baseline_days)).strftime("%Y%m%d")
    baseline_end = (today - timedelta(days=args.recent_days + 1)).strftime("%Y%m%d")

    print(f"chihou データ健全性チェック  直近={recent_start}〜{end}  baseline={baseline_start}〜{baseline_end}")
    print("=" * 78)

    conn = psycopg2.connect(DSN)
    cur = conn.cursor()

    results = [
        check_external_index_coverage(cur, recent_start, end, baseline_start, baseline_end),
        check_odds_completeness(cur, recent_start, end, baseline_start, baseline_end),
        check_index_calculation_gap(cur, recent_start, end),
        check_composite_index_distribution(cur, recent_start, end, baseline_start, baseline_end),
    ]
    cur.close()
    conn.close()

    print("=" * 78)
    if all(results):
        print("total: OK")
        sys.exit(0)
    else:
        print("total: WARN — 上記のいずれかが異常。原因調査が必要")
        sys.exit(1)


if __name__ == "__main__":
    main()
