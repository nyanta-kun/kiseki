"""breakaway (先頭集団抜け出し) ベースの購入戦略評価。

「上位 N 頭が後続から差を付けて抜け出しているレース」を抽出し、
その先頭集団内の高オッズ馬を購入対象にする戦略を評価。

定義:
  - top2_breakaway: rank≤2 ∧ (rank=2 と rank=3 の差) ≥ threshold
  - top3_breakaway: rank≤3 ∧ (rank=3 と rank=4 の差) ≥ threshold
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

QUERY = """
WITH ranked AS (
  SELECT
    ci.race_id, ci.horse_id, ci.composite_index,
    re.horse_number AS horse_no,
    rr.finish_position, rr.win_popularity, rr.win_odds, rr.place_odds,
    r.head_count,
    RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.composite_index DESC) AS rk
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
SELECT race_id, horse_id, horse_no, composite_index,
       finish_position, win_popularity, win_odds, place_odds, head_count, rk
FROM ranked;
"""

GAP_QUERY = """
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
  MAX(CASE WHEN rk = 1 THEN composite_index END) AS t1,
  MAX(CASE WHEN rk = 2 THEN composite_index END) AS t2,
  MAX(CASE WHEN rk = 3 THEN composite_index END) AS t3,
  MAX(CASE WHEN rk = 4 THEN composite_index END) AS t4,
  MAX(CASE WHEN rk = 5 THEN composite_index END) AS t5
FROM ranked
GROUP BY race_id;
"""


def evaluate(rows: list[dict], desc: str, pred) -> dict:
    matched = [r for r in rows if pred(r)]
    n = len(matched)
    if n == 0:
        return {"desc": desc, "n": 0}
    win = sum(1 for r in matched if r["finish_position"] == 1)
    place = sum(1 for r in matched if r["finish_position"] is not None and r["finish_position"] <= 3)
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

    print(f"Loading horses for {args.start}-{args.end}...", file=sys.stderr)
    cur.execute(QUERY, {"start": args.start, "end": args.end})
    cols = [d[0] for d in cur.description]
    all_rows = [dict(zip(cols, row)) for row in cur.fetchall()]

    cur.execute(GAP_QUERY, {"start": args.start, "end": args.end})
    gaps: dict[int, dict] = {}
    for row in cur.fetchall():
        rid, t1, t2, t3, t4, t5 = row
        gaps[rid] = {
            "t1": float(t1) if t1 else 0,
            "t2": float(t2) if t2 else 0,
            "t3": float(t3) if t3 else 0,
            "t4": float(t4) if t4 else 0,
            "t5": float(t5) if t5 else 0,
        }
    cur.close()
    conn.close()
    print(f"  {len(all_rows):,} horse-races, {len(gaps):,} races", file=sys.stderr)

    # 各 row に gap 情報付与
    for r in all_rows:
        g = gaps.get(r["race_id"], {})
        r["t1_t2_gap"] = g.get("t1", 0) - g.get("t2", 0)  # rank=1 と rank=2 の差
        r["t2_t3_gap"] = g.get("t2", 0) - g.get("t3", 0)  # 上位2頭が3位から抜け出す差
        r["t3_t4_gap"] = g.get("t3", 0) - g.get("t4", 0)  # 上位3頭が4位から抜け出す差

    # --- 戦略定義 ---
    # 「上位X頭が後続から抜け出し」のうち高オッズ馬

    strategies = []

    # -- TOP 2 breakaway: 上位2頭が3位と差≥X、その中の高オッズ馬 (オッズ≥10/5) --
    for gap_thresh in (3, 5, 7, 10):
        # 上位2頭で (1位は当然) breakaway なら買う
        strategies.append((
            f"top2_breakaway(gap≥{gap_thresh}) ∧ rank≤2 ∧ オッズ≥10",
            lambda r, g=gap_thresh: r["rk"] <= 2 and r["t2_t3_gap"] >= g
                and r["win_odds"] is not None and float(r["win_odds"]) >= 10,
        ))
        strategies.append((
            f"top2_breakaway(gap≥{gap_thresh}) ∧ rank≤2 ∧ オッズ≥5",
            lambda r, g=gap_thresh: r["rk"] <= 2 and r["t2_t3_gap"] >= g
                and r["win_odds"] is not None and float(r["win_odds"]) >= 5,
        ))
        # rank=2 の高オッズだけ
        strategies.append((
            f"top2_breakaway(gap≥{gap_thresh}) ∧ rank=2 ∧ オッズ≥10",
            lambda r, g=gap_thresh: r["rk"] == 2 and r["t2_t3_gap"] >= g
                and r["win_odds"] is not None and float(r["win_odds"]) >= 10,
        ))

    # -- TOP 3 breakaway: 上位3頭が4位と差≥X、その中の高オッズ馬 --
    for gap_thresh in (3, 5, 7):
        strategies.append((
            f"top3_breakaway(gap≥{gap_thresh}) ∧ rank≤3 ∧ オッズ≥10",
            lambda r, g=gap_thresh: r["rk"] <= 3 and r["t3_t4_gap"] >= g
                and r["win_odds"] is not None and float(r["win_odds"]) >= 10,
        ))
        strategies.append((
            f"top3_breakaway(gap≥{gap_thresh}) ∧ rank=2 or 3 ∧ オッズ≥10",
            lambda r, g=gap_thresh: r["rk"] in (2, 3) and r["t3_t4_gap"] >= g
                and r["win_odds"] is not None and float(r["win_odds"]) >= 10,
        ))
        strategies.append((
            f"top3_breakaway(gap≥{gap_thresh}) ∧ rank=3 ∧ オッズ≥10",
            lambda r, g=gap_thresh: r["rk"] == 3 and r["t3_t4_gap"] >= g
                and r["win_odds"] is not None and float(r["win_odds"]) >= 10,
        ))

    # -- 「先頭集団内で最も高オッズ馬」 (per race の選択) --
    # → 全 race で「breakaway 成立 + 先頭集団内最高オッズ馬」を抽出する
    races_top2_breakaway: dict[int, list[dict]] = {}
    races_top3_breakaway: dict[int, list[dict]] = {}
    for r in all_rows:
        if r["t2_t3_gap"] >= 3 and r["rk"] <= 2:
            races_top2_breakaway.setdefault(r["race_id"], []).append(r)
        if r["t3_t4_gap"] >= 3 and r["rk"] <= 3:
            races_top3_breakaway.setdefault(r["race_id"], []).append(r)

    def highest_odds_in_group(rows_per_race: dict[int, list[dict]]) -> set[tuple[int, int]]:
        """各レースの先頭集団内で最高オッズ馬の (race_id, horse_id) を返す。"""
        out: set[tuple[int, int]] = set()
        for rid, group in rows_per_race.items():
            valid = [g for g in group if g["win_odds"] is not None]
            if not valid:
                continue
            best = max(valid, key=lambda g: float(g["win_odds"]))
            out.add((rid, best["horse_id"]))
        return out

    def make_pred_set(s: set[tuple[int, int]]):
        def pred(r):
            return (r["race_id"], r["horse_id"]) in s
        return pred

    for gap_thresh in (3, 5, 7, 10):
        # Top2 breakaway 内最高オッズ
        races_local: dict[int, list[dict]] = {}
        for r in all_rows:
            if r["t2_t3_gap"] >= gap_thresh and r["rk"] <= 2:
                races_local.setdefault(r["race_id"], []).append(r)
        s = highest_odds_in_group(races_local)
        strategies.append((
            f"top2_breakaway(gap≥{gap_thresh}) 内最高オッズ馬",
            make_pred_set(s),
        ))

        # Top3 breakaway 内最高オッズ
        races_local = {}
        for r in all_rows:
            if r["t3_t4_gap"] >= gap_thresh and r["rk"] <= 3:
                races_local.setdefault(r["race_id"], []).append(r)
        s = highest_odds_in_group(races_local)
        strategies.append((
            f"top3_breakaway(gap≥{gap_thresh}) 内最高オッズ馬",
            make_pred_set(s),
        ))

    # 評価
    print(f"\n=== {args.start}〜{args.end} breakaway 戦略 ===\n")
    print(f"{'戦略':<60}{'n':>7}{'勝率':>9}{'複勝率':>10}{'単ROI':>10}{'複ROI':>10}")
    print("-" * 105)
    results = []
    for desc, pred in strategies:
        m = evaluate(all_rows, desc, pred)
        results.append(m)
        if m["n"] == 0:
            print(f"{desc:<60}{'0':>7}{'—':>9}{'—':>10}{'—':>10}{'—':>10}")
            continue
        print(
            f"{desc:<60}{m['n']:>7}"
            f"{m['win_pct']:>8.2f}%{m['place_pct']:>9.2f}%"
            f"{m['win_roi']:>10.3f}{m['place_roi']:>10.3f}"
        )

    print("\n=== ROI ≥ 1.0 の戦略 (n≥30) ===\n")
    pos = [m for m in results if m["n"] >= 30 and (m.get("win_roi", 0) >= 1.0 or m.get("place_roi", 0) >= 1.0)]
    if not pos:
        print("  (該当なし)")
    else:
        pos.sort(key=lambda m: -m.get("win_roi", 0))
        for m in pos:
            kind = []
            if m.get("win_roi", 0) >= 1.0:
                kind.append(f"単ROI {m['win_roi']:.3f}")
            if m.get("place_roi", 0) >= 1.0:
                kind.append(f"複ROI {m['place_roi']:.3f}")
            print(f"  ✓ {m['desc']:<60} n={m['n']:>5}  {' / '.join(kind)}")


if __name__ == "__main__":
    main()
