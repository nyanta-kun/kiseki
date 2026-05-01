"""指数分布ベースの購入戦略評価。

「指数1位」だけでなく、以下の軸で購入対象を拡張・絞り込みする:
  - 絶対値: composite_index >= X
  - ランク: idx_rank <= N (top1-top3)
  - 1位vs2位差: top1.composite - top2.composite >= Y
  - レース平均差: composite - race_avg >= Z
組合せでROI最適点を探す。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import psycopg2

# 全馬データ取得 + race内統計
QUERY = """
WITH ranked AS (
  SELECT
    ci.race_id, ci.horse_id,
    ci.composite_index,
    re.horse_number AS horse_no,
    rr.finish_position, rr.win_popularity, rr.win_odds, rr.place_odds,
    r.head_count, r.distance,
    RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.composite_index DESC) AS idx_rank,
    AVG(ci.composite_index) OVER (PARTITION BY ci.race_id) AS race_avg,
    MAX(ci.composite_index) OVER (PARTITION BY ci.race_id) AS race_max,
    LAG(ci.composite_index) OVER (PARTITION BY ci.race_id ORDER BY ci.composite_index DESC)
      AS higher_score
  FROM keiba.calculated_indices ci
  JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
  JOIN keiba.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
  JOIN keiba.races r ON r.id = ci.race_id
  WHERE ci.version = 26
    AND r.head_count >= 8
    AND r.date BETWEEN %(start)s AND %(end)s
    AND COALESCE(rr.abnormality_code, 0) = 0
    AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
)
SELECT
  race_id, horse_id, composite_index, horse_no,
  finish_position, win_popularity, win_odds, place_odds,
  head_count, distance, idx_rank, race_avg, race_max,
  -- 自分のスコアと race_max の差(1位馬は0)
  (race_max - composite_index) AS gap_from_top,
  -- 1位馬の場合: 2位との差 (それ以外: NULL)
  CASE WHEN idx_rank = 1 THEN NULL ELSE NULL END AS placeholder
FROM ranked;
"""

# top1 と top2 の差を別途取得 (LAG では 2位との差を取りにくい為)
TOP_GAP_QUERY = """
WITH ranked AS (
  SELECT
    ci.race_id, ci.composite_index,
    RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.composite_index DESC) AS rk
  FROM keiba.calculated_indices ci
  JOIN keiba.race_results rr ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
  JOIN keiba.races r ON r.id = ci.race_id
  WHERE ci.version = 26
    AND r.head_count >= 8
    AND r.date BETWEEN %(start)s AND %(end)s
    AND COALESCE(rr.abnormality_code, 0) = 0
    AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
)
SELECT
  race_id,
  MAX(CASE WHEN rk = 1 THEN composite_index END) AS top1,
  MAX(CASE WHEN rk = 2 THEN composite_index END) AS top2
FROM ranked
GROUP BY race_id;
"""


def evaluate(rows: list[dict], desc: str, pred) -> dict:
    matched = [r for r in rows if pred(r)]
    n = len(matched)
    if n == 0:
        return {"desc": desc, "n": 0}
    win = sum(1 for r in matched if r["finish_position"] == 1)
    place = sum(
        1 for r in matched if r["finish_position"] is not None and r["finish_position"] <= 3
    )
    win_returns = sum(float(r["win_odds"] or 0) for r in matched if r["finish_position"] == 1)
    place_returns = sum(
        float(r["place_odds"] or 1.5) for r in matched
        if r["finish_position"] is not None and r["finish_position"] <= 3
    )
    return {
        "desc": desc,
        "n": n,
        "win_pct": win / n * 100,
        "place_pct": place / n * 100,
        "win_roi": win_returns / n,
        "place_roi": place_returns / n,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20230501")
    p.add_argument("--end", default="20260501")
    args = p.parse_args()

    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    print(f"Loading all horses for {args.start}-{args.end}...", file=sys.stderr)
    cur.execute(QUERY, {"start": args.start, "end": args.end})
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    print(f"  {len(rows):,} horse-races", file=sys.stderr)

    # top1 vs top2 のギャップ
    cur.execute(TOP_GAP_QUERY, {"start": args.start, "end": args.end})
    top12 = {r[0]: (float(r[1] or 0), float(r[2] or 0)) for r in cur.fetchall()}
    cur.close()
    conn.close()

    # 各 row に top1_top2_gap (1位と2位の差) を付与
    for r in rows:
        t1, t2 = top12.get(r["race_id"], (0, 0))
        r["top1_top2_gap"] = t1 - t2  # 1位と2位の差 (>=0)
        r["composite_index"] = float(r["composite_index"]) if r["composite_index"] else 0.0
        r["race_avg"] = float(r["race_avg"]) if r["race_avg"] else 0.0
        r["race_max"] = float(r["race_max"]) if r["race_max"] else 0.0
        r["gap_from_top"] = float(r["gap_from_top"]) if r["gap_from_top"] is not None else 0.0
        r["dev_from_avg"] = r["composite_index"] - r["race_avg"]

    strategies = []

    # === 絶対値ベース (rank 不問) ===
    strategies.append(("composite≥70 全馬", lambda r: r["composite_index"] >= 70))
    strategies.append(("composite≥65 全馬", lambda r: r["composite_index"] >= 65))
    strategies.append(("composite≥60 全馬", lambda r: r["composite_index"] >= 60))

    # === ランクベース ===
    strategies.append(("rank=1 (現状)", lambda r: r["idx_rank"] == 1))
    strategies.append(("rank≤2 全馬 (1+2位両買い)", lambda r: r["idx_rank"] <= 2))
    strategies.append(("rank≤3 全馬", lambda r: r["idx_rank"] <= 3))
    strategies.append(("rank=2 のみ", lambda r: r["idx_rank"] == 2))
    strategies.append(("rank=3 のみ", lambda r: r["idx_rank"] == 3))

    # === 絶対値 × ランク ===
    strategies.append(("rank=1 ∧ composite≥65", lambda r: r["idx_rank"] == 1 and r["composite_index"] >= 65))
    strategies.append(("rank=1 ∧ composite≥70", lambda r: r["idx_rank"] == 1 and r["composite_index"] >= 70))
    strategies.append(("rank≤2 ∧ composite≥60", lambda r: r["idx_rank"] <= 2 and r["composite_index"] >= 60))
    strategies.append(("rank≤2 ∧ composite≥65", lambda r: r["idx_rank"] <= 2 and r["composite_index"] >= 65))

    # === margin ベース (1位馬のみ) ===
    strategies.append(("rank=1 ∧ 2位差≥3", lambda r: r["idx_rank"] == 1 and r["top1_top2_gap"] >= 3))
    strategies.append(("rank=1 ∧ 2位差≥5", lambda r: r["idx_rank"] == 1 and r["top1_top2_gap"] >= 5))
    strategies.append(("rank=1 ∧ 2位差≥7", lambda r: r["idx_rank"] == 1 and r["top1_top2_gap"] >= 7))
    strategies.append(("rank=1 ∧ 2位差≥10", lambda r: r["idx_rank"] == 1 and r["top1_top2_gap"] >= 10))

    # === レース平均差ベース ===
    strategies.append(("dev_from_avg≥5 全馬", lambda r: r["dev_from_avg"] >= 5))
    strategies.append(("dev_from_avg≥10 全馬", lambda r: r["dev_from_avg"] >= 10))
    strategies.append(("rank=1 ∧ dev_from_avg≥10", lambda r: r["idx_rank"] == 1 and r["dev_from_avg"] >= 10))
    strategies.append(("rank=1 ∧ dev_from_avg≥15", lambda r: r["idx_rank"] == 1 and r["dev_from_avg"] >= 15))

    # === 複合 (オッズ含む) ===
    strategies.append((
        "rank=1 ∧ 2位差≥5 ∧ オッズ≥10",
        lambda r: r["idx_rank"] == 1 and r["top1_top2_gap"] >= 5
                  and r["win_odds"] is not None and float(r["win_odds"]) >= 10,
    ))
    strategies.append((
        "rank=1 ∧ 2位差≥7 ∧ オッズ≥5",
        lambda r: r["idx_rank"] == 1 and r["top1_top2_gap"] >= 7
                  and r["win_odds"] is not None and float(r["win_odds"]) >= 5,
    ))
    strategies.append((
        "composite≥65 全馬 ∧ オッズ≥5",
        lambda r: r["composite_index"] >= 65
                  and r["win_odds"] is not None and float(r["win_odds"]) >= 5,
    ))
    strategies.append((
        "composite≥65 全馬 ∧ オッズ≥10",
        lambda r: r["composite_index"] >= 65
                  and r["win_odds"] is not None and float(r["win_odds"]) >= 10,
    ))
    strategies.append((
        "rank≤2 ∧ composite≥60 ∧ オッズ≥10",
        lambda r: r["idx_rank"] <= 2 and r["composite_index"] >= 60
                  and r["win_odds"] is not None and float(r["win_odds"]) >= 10,
    ))

    # === 大穴買い (低ランクでも条件次第で買う) ===
    strategies.append((
        "rank≤3 ∧ オッズ≥10",
        lambda r: r["idx_rank"] <= 3
                  and r["win_odds"] is not None and float(r["win_odds"]) >= 10,
    ))
    strategies.append((
        "rank=2 ∧ オッズ≥10",
        lambda r: r["idx_rank"] == 2
                  and r["win_odds"] is not None and float(r["win_odds"]) >= 10,
    ))
    strategies.append((
        "rank=3 ∧ オッズ≥10",
        lambda r: r["idx_rank"] == 3
                  and r["win_odds"] is not None and float(r["win_odds"]) >= 10,
    ))

    # 評価出力
    print(f"\n=== {args.start}〜{args.end} 指数分布ベース戦略 (n={len(rows):,}) ===\n")
    print(f"{'戦略':<48}{'n':>7}{'勝率':>9}{'複勝率':>10}{'単ROI':>10}{'複ROI':>10}")
    print("-" * 95)
    results = []
    for desc, pred in strategies:
        m = evaluate(rows, desc, pred)
        results.append(m)
        if m["n"] == 0:
            print(f"{desc:<48}{'0':>7}{'—':>9}{'—':>10}{'—':>10}{'—':>10}")
            continue
        print(
            f"{desc:<48}{m['n']:>7}"
            f"{m['win_pct']:>8.2f}%{m['place_pct']:>9.2f}%"
            f"{m['win_roi']:>10.3f}{m['place_roi']:>10.3f}"
        )

    print("\n=== ROI ≥ 1.0 の戦略 (期待値プラス, n≥30) ===\n")
    positive = [
        m for m in results
        if m["n"] >= 30 and (m.get("win_roi", 0) >= 1.0 or m.get("place_roi", 0) >= 1.0)
    ]
    if not positive:
        print("  (該当なし)")
    else:
        positive.sort(key=lambda m: -m.get("win_roi", 0))
        for m in positive:
            kind = []
            if m.get("win_roi", 0) >= 1.0:
                kind.append(f"単ROI {m['win_roi']:.3f}")
            if m.get("place_roi", 0) >= 1.0:
                kind.append(f"複ROI {m['place_roi']:.3f}")
            print(f"  ✓ {m['desc']:<48} n={m['n']:>5}  {' / '.join(kind)}")


if __name__ == "__main__":
    main()
