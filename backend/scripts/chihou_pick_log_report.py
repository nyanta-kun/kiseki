"""地方競馬 注目馬の前向き記録を集計する（`chihou.place_pick_races` / `place_picks`）。

記録の作られ方は `src/services/chihou_place_pick_log.py` を参照。
**発走前に撮ったスナップショットだけを見る**ので、ここの数字は look-ahead を含まない。

使い方:

    python scripts/chihou_pick_log_report.py --start 20260815 --end 20260930

出るもの:

  1. カバレッジ — 何レース撮れたか / 撮り逃しはどれくらいか
  2. 推奨の成績 — 複勝率・レース単位の的中・複勝ROI（`place_payout_odds` がある分のみ）
  3. 棄権の答え合わせ — 推奨あり/なしで「人気薄が複勝圏に来た率」が割れているか（台帳 11.3）
  4. 別案の反実仮想 — 指数◯位内 × 最大◯頭にしていたら（台帳 12.5 の保留仮説）

⚠️ **標本が貯まるまで結論を出さないこと。** 台帳 11.4 の運用点は網羅率 14%・
1レース1.4頭で、複勝率 28.2%（±SE）を確認するには数百件が要る。
月次で眺めて「壊れていないか」を見るのが当面の用途。
"""

from __future__ import annotations

import argparse
import os
import sys
from math import sqrt
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_root.parent / ".env")

import psycopg2  # noqa: E402

DSN = (
    f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
    f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
    f"password={os.getenv('DB_PASSWORD')}"
)

COVERAGE_SQL = """
    SELECT
      (SELECT COUNT(*) FROM chihou.races r
        WHERE r.date BETWEEN %(start)s AND %(end)s AND r.course <> '83') AS n_races,
      COUNT(*)                                            AS n_logged,
      COUNT(*) FILTER (WHERE settled_at IS NOT NULL)      AS n_settled,
      COUNT(*) FILTER (WHERE n_picked > 0)                AS n_with_pick,
      SUM(n_picked)                                       AS n_picks,
      AVG(lead_minutes)                                   AS avg_lead,
      COUNT(DISTINCT rule_version)                        AS n_rule_versions,
      COUNT(DISTINCT index_version)                       AS n_index_versions
    FROM chihou.place_pick_races
    WHERE date BETWEEN %(start)s AND %(end)s
"""

SKIP_SQL = """
    SELECT COALESCE(skip_reason, '(推奨あり)') AS reason, COUNT(*)
    FROM chihou.place_pick_races
    WHERE date BETWEEN %(start)s AND %(end)s
    GROUP BY 1 ORDER BY 2 DESC
"""

PICK_SQL = """
    SELECT p.index_rank, p.pop_rank, p.finish_position, p.abnormality_code,
           p.place_payout_odds, p.pre_win_odds
    FROM chihou.place_picks p
    JOIN chihou.place_pick_races lr ON lr.id = p.pick_race_id
    WHERE lr.date BETWEEN %(start)s AND %(end)s
      AND lr.settled_at IS NOT NULL AND p.is_picked
"""

ABSTAIN_SQL = """
    SELECT (n_picked > 0) AS has_pick,
           COUNT(*)                                  AS n_races,
           COUNT(*) FILTER (WHERE upset_placed)      AS n_upset_placed,
           COUNT(*) FILTER (WHERE race_hit)          AS n_hit
    FROM chihou.place_pick_races
    WHERE date BETWEEN %(start)s AND %(end)s AND settled_at IS NOT NULL
    GROUP BY 1 ORDER BY 1
"""

# 反実仮想: 適格判定のうち「指数◯位内」だけを差し替え、レースごとに上位 N 頭を採る。
# 人気帯・シェア・頭数のゲートは記録時のものをそのまま使う（is_eligible ではなく
# pop_rank / index_rank から引き直すので、指数順位だけを動かせる）。
WHATIF_SQL = """
    WITH cand AS (
      SELECT lr.id AS lr_id, p.horse_number, p.index_rank, p.finish_position,
             p.abnormality_code, p.place_payout_odds,
             ROW_NUMBER() OVER (PARTITION BY lr.id ORDER BY p.index_rank, p.horse_number) AS ord
      FROM chihou.place_picks p
      JOIN chihou.place_pick_races lr ON lr.id = p.pick_race_id
      WHERE lr.date BETWEEN %(start)s AND %(end)s
        AND lr.settled_at IS NOT NULL
        AND lr.top3_share < %(share)s
        AND lr.head_count_used >= %(min_head)s
        AND p.pop_rank >= %(min_pop)s
        AND p.index_rank <= %(max_rank)s
    )
    SELECT COUNT(*) AS n,
           COUNT(*) FILTER (
             WHERE finish_position <= 3 AND COALESCE(abnormality_code, 0) = 0
           ) AS n_place,
           COUNT(DISTINCT lr_id) AS n_races,
           COUNT(DISTINCT lr_id) FILTER (
             WHERE finish_position <= 3 AND COALESCE(abnormality_code, 0) = 0
           ) AS n_race_hit
    FROM cand WHERE ord <= %(max_picks)s
"""


def _pct(num: int, den: int) -> str:
    if not den:
        return "  -  "
    return f"{num / den * 100:5.1f}%"


def _se(p: float, n: int) -> float:
    return sqrt(p * (1 - p) / n) * 100 if n else 0.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True, help="開始日 YYYYMMDD")
    ap.add_argument("--end", required=True, help="終了日 YYYYMMDD")
    args = ap.parse_args()
    span = {"start": args.start, "end": args.end}

    conn = psycopg2.connect(DSN)
    cur = conn.cursor()

    print("=" * 72)
    print(f"  地方 注目馬 前向き記録  {args.start} 〜 {args.end}")
    print("=" * 72)

    cur.execute(COVERAGE_SQL, span)
    (n_races, n_logged, n_settled, n_with_pick, n_picks, avg_lead,
     n_rule_v, n_index_v) = cur.fetchone()
    if not n_logged:
        print("\n記録がありません。cron が動いているか確認してください。")
        return
    print("\n[カバレッジ]")
    print(f"  開催レース {n_races or 0:,} / 記録 {n_logged:,} ({_pct(n_logged, n_races or 0)})")
    print(f"  結果確定済み {n_settled:,}  推奨が出たレース {n_with_pick:,} "
          f"({_pct(n_with_pick, n_logged)})  推奨頭数 {n_picks or 0:,}")
    print(f"  平均リード {float(avg_lead or 0):.1f} 分前")
    if n_rule_v > 1 or n_index_v > 1:
        print(f"  ⚠️ 期間内に rule_version {n_rule_v} 種 / index_version {n_index_v} 種が混在。"
              " 世代を分けて集計すること")

    print("\n[推奨が出なかった理由]")
    cur.execute(SKIP_SQL, span)
    for reason, cnt in cur.fetchall():
        print(f"  {reason:<14} {cnt:>6,}  ({_pct(cnt, n_logged)})")

    cur.execute(PICK_SQL, span)
    rows = cur.fetchall()
    settled = [r for r in rows if r[2] is not None]
    print("\n[推奨馬の成績]")
    if not settled:
        print("  確定した推奨がまだありません")
    else:
        placed = [r for r in settled if r[2] <= 3 and (r[3] or 0) == 0]
        rate = len(placed) / len(settled)
        print(f"  n={len(settled):,}  複勝率 {rate*100:5.1f}%  (±{_se(rate, len(settled)):.1f}pt)")
        print("    参考: DISCOVERY での期待値は 28.2%（台帳 11.4）")
        with_odds = [r for r in settled if r[4] is not None]
        if with_odds:
            ret = sum(r[4] for r in with_odds if r[2] <= 3 and (r[3] or 0) == 0)
            print(f"  複勝ROI {float(ret) / len(with_odds):.3f}  (払戻が取れた {len(with_odds):,} 頭)")
            print("    ※ 的中率の設計であって収支の設計ではない（台帳 10.1）")
        pre = [float(r[5]) for r in settled if r[5] is not None]
        if pre:
            pre.sort()
            print(f"  発走前単勝オッズ 中央値 {pre[len(pre)//2]:.1f} 倍")

    print("\n[棄権の答え合わせ] — 人気薄が実際に複勝圏へ来たレースの割合")
    cur.execute(ABSTAIN_SQL, span)
    for has_pick, n_r, n_up, n_hit in cur.fetchall():
        label = "推奨あり" if has_pick else "棄権   "
        extra = f"  レース的中 {_pct(n_hit or 0, n_r)}" if has_pick else ""
        print(f"  {label}  {n_r:>5,} レース   人気薄が複勝圏 {_pct(n_up or 0, n_r)}{extra}")
    print("    参考: DISCOVERY では 推奨あり 80.4% / 棄権 51.0%（台帳 11.3）")

    print("\n[反実仮想] 指数順位 × 採用頭数（記録時の人気帯・シェア・頭数ゲートは固定）")
    print("  ルール              推奨   複勝率   R的中")
    for max_rank in (2, 3, 5, 6):
        for max_picks in (1, 2):
            cur.execute(WHATIF_SQL, {
                **span, "share": 0.63, "min_head": 8, "min_pop": 6,
                "max_rank": max_rank, "max_picks": max_picks,
            })
            n, n_place, n_r, n_rhit = cur.fetchone()
            if not n:
                continue
            mark = "*" if (max_rank, max_picks) == (5, 2) else " "
            print(f" {mark}指数{max_rank}位内 × 最大{max_picks}頭 {n:>6,}  "
                  f"{_pct(n_place, n)}  {_pct(n_rhit, n_r)}")
    print("  * = 現行の運用点。他は事後の比較であり、乗り換えの根拠にはしないこと")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
