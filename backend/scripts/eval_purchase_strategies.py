"""購入条件最適化シミュレーション。

v26 指数1位馬を「いつ買い、いつ見送るか」のフィルタを test 期間で評価。
- 人気・オッズ・DM・anagusa・外部指数 (kichiuma / netkeiba) の単独/組合せ
- 単勝 ROI > 1.0 (期待値プラス) の購入条件を探す
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

# JRA → sekito コードマッピング
_JRA_TO_SEKITO: dict[str, str] = {
    "01": "JSPK", "02": "JHKD", "03": "JFKS", "04": "JNGT", "05": "JTOK",
    "06": "JNKY", "07": "JCKO", "08": "JKYO", "09": "JHSN", "10": "JKKR",
}

# 全 1位馬を必要属性付きで取得
QUERY = """
WITH ranked AS (
  SELECT
    ci.race_id, ci.horse_id,
    ci.composite_index,
    re.horse_number AS horse_no,
    re.jvan_time_dm, re.jvan_battle_dm,
    rr.finish_position, rr.win_popularity, rr.win_odds, rr.place_odds, rr.passing_4,
    r.head_count, r.date AS race_date, r.course AS jra_course, r.race_number,
    -- レース内ランク
    RANK() OVER (PARTITION BY ci.race_id ORDER BY ci.composite_index DESC) AS idx_rank,
    RANK() OVER (PARTITION BY ci.race_id ORDER BY re.jvan_time_dm DESC NULLS LAST) AS time_dm_rank,
    RANK() OVER (PARTITION BY ci.race_id ORDER BY re.jvan_battle_dm DESC NULLS LAST) AS battle_dm_rank
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
SELECT * FROM ranked WHERE idx_rank = 1;
"""

# 外部指数を取得（race ごとに 1位馬の horse_no と一致するか確認）
EXTERNAL_QUERY = """
WITH netkeiba_ranks AS (
  SELECT
    n.date AS race_date,
    n.course_code AS sekito_course,
    n.race_no AS race_number,
    n.horse_no,
    RANK() OVER (PARTITION BY n.date, n.course_code, n.race_no
                 ORDER BY (CASE WHEN n.idx_course ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN n.idx_course::numeric ELSE NULL END) DESC NULLS LAST) AS nb_course_rank,
    RANK() OVER (PARTITION BY n.date, n.course_code, n.race_no
                 ORDER BY (CASE WHEN n.idx_ave ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN n.idx_ave::numeric ELSE NULL END) DESC NULLS LAST) AS nb_ave_rank
  FROM sekito.netkeiba n
  WHERE n.date BETWEEN %(date_start)s AND %(date_end)s
),
kichiuma_ranks AS (
  SELECT
    k.date AS race_date,
    k.course_code AS sekito_course,
    k.race_no AS race_number,
    k.horse_no,
    RANK() OVER (PARTITION BY k.date, k.course_code, k.race_no
                 ORDER BY k.sp_score DESC NULLS LAST) AS km_rank
  FROM sekito.kichiuma k
  WHERE k.date BETWEEN %(date_start)s AND %(date_end)s
)
SELECT
  COALESCE(n.race_date, k.race_date) AS race_date,
  COALESCE(n.sekito_course, k.sekito_course) AS sekito_course,
  COALESCE(n.race_number, k.race_number) AS race_number,
  COALESCE(n.horse_no, k.horse_no) AS horse_no,
  n.nb_course_rank, n.nb_ave_rank, k.km_rank
FROM netkeiba_ranks n
FULL OUTER JOIN kichiuma_ranks k
  ON k.race_date = n.race_date
 AND k.sekito_course = n.sekito_course
 AND k.race_number = n.race_number
 AND k.horse_no = n.horse_no;
"""

# anagusa
ANAGUSA_QUERY = """
SELECT date, course_code, race_no, horse_no, rank
FROM sekito.anagusa
WHERE date BETWEEN %(date_start)s AND %(date_end)s;
"""


def fmt_pct(v: float) -> str:
    return f"{v * 100:.2f}%"


def fmt_roi(returns: float, n: int) -> str:
    return f"{returns / n:.3f}" if n > 0 else "—"


def evaluate_strategy(
    rows: list[dict],
    name: str,
    desc: str,
    pred,
) -> dict:
    """戦略 pred(row) -> bool で行をフィルタし指標を計算。"""
    matched = [r for r in rows if pred(r)]
    n = len(matched)
    if n == 0:
        return {"name": name, "desc": desc, "n": 0}

    win = sum(1 for r in matched if r["finish_position"] == 1)
    place = sum(1 for r in matched if r["finish_position"] is not None and r["finish_position"] <= 3)
    win_returns = sum(
        float(r["win_odds"] or 0) for r in matched if r["finish_position"] == 1
    )
    place_returns = sum(
        float(r["place_odds"] or 1.5) for r in matched
        if r["finish_position"] is not None and r["finish_position"] <= 3
    )

    return {
        "name": name,
        "desc": desc,
        "n": n,
        "win_pct": win / n,
        "place_pct": place / n,
        "win_roi": win_returns / n,
        "place_roi": place_returns / n,
    }


def load_data(start: str, end: str) -> list[dict]:
    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()

    print(f"Loading top1 horses for {start}-{end}...", file=sys.stderr)
    cur.execute(QUERY, {"start": start, "end": end})
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    print(f"  loaded {len(rows)} top1 records", file=sys.stderr)

    # 外部指数: date は string なので date_format 変換
    date_start = f"{start[:4]}-{start[4:6]}-{start[6:8]}"
    date_end = f"{end[:4]}-{end[4:6]}-{end[6:8]}"

    print("Loading external ranks (netkeiba/kichiuma)...", file=sys.stderr)
    cur.execute(EXTERNAL_QUERY, {"date_start": date_start, "date_end": date_end})
    ext_cols = [d[0] for d in cur.description]
    ext_rows = [dict(zip(ext_cols, row)) for row in cur.fetchall()]
    # key: (date_str, sekito_course, race_number, horse_no)
    ext_map: dict[tuple, dict] = {}
    for er in ext_rows:
        if er["race_date"] is None:
            continue
        key = (
            er["race_date"].strftime("%Y%m%d"),
            er["sekito_course"],
            er["race_number"],
            er["horse_no"],
        )
        ext_map[key] = er
    print(f"  loaded {len(ext_map)} external rank records", file=sys.stderr)

    print("Loading anagusa picks...", file=sys.stderr)
    cur.execute(ANAGUSA_QUERY, {"date_start": date_start, "date_end": date_end})
    ana_cols = [d[0] for d in cur.description]
    ana_rows = [dict(zip(ana_cols, row)) for row in cur.fetchall()]
    ana_map: dict[tuple, str] = {}
    for ar in ana_rows:
        key = (
            ar["date"].strftime("%Y%m%d"),
            ar["course_code"],
            ar["race_no"],
            ar["horse_no"],
        )
        ana_map[key] = ar["rank"]
    print(f"  loaded {len(ana_map)} anagusa picks", file=sys.stderr)

    cur.close()
    conn.close()

    # 各 1位馬に外部・anagusa を付与
    for r in rows:
        sekito_course = _JRA_TO_SEKITO.get(r["jra_course"])
        if sekito_course is None:
            r["nb_course_rank"] = None
            r["nb_ave_rank"] = None
            r["km_rank"] = None
            r["anagusa_rank"] = None
            continue
        key = (r["race_date"], sekito_course, r["race_number"], r["horse_no"])
        ext = ext_map.get(key)
        if ext:
            r["nb_course_rank"] = ext["nb_course_rank"]
            r["nb_ave_rank"] = ext["nb_ave_rank"]
            r["km_rank"] = ext["km_rank"]
        else:
            r["nb_course_rank"] = None
            r["nb_ave_rank"] = None
            r["km_rank"] = None
        r["anagusa_rank"] = ana_map.get(key)

    return rows


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20230501")
    p.add_argument("--end", default="20260501")
    args = p.parse_args()

    rows = load_data(args.start, args.end)

    strategies = []
    # ベースライン
    strategies.append(("baseline", "v26 1位 (現状)", lambda r: True))

    # 人気フィルタ
    strategies.append(("pop_eq1", "v26 1位 ∧ 1番人気のみ", lambda r: r["win_popularity"] == 1))
    strategies.append(("pop_le2", "v26 1位 ∧ 2番人気以内", lambda r: r["win_popularity"] is not None and r["win_popularity"] <= 2))
    strategies.append(("pop_le3", "v26 1位 ∧ 3番人気以内", lambda r: r["win_popularity"] is not None and r["win_popularity"] <= 3))
    strategies.append(("pop_le5", "v26 1位 ∧ 5番人気以内", lambda r: r["win_popularity"] is not None and r["win_popularity"] <= 5))
    strategies.append(("pop_ge6", "v26 1位 ∧ 6番人気以下", lambda r: r["win_popularity"] is not None and r["win_popularity"] >= 6))

    # オッズフィルタ
    strategies.append(("odds_le_5", "v26 1位 ∧ 単勝オッズ≤5", lambda r: r["win_odds"] is not None and float(r["win_odds"]) <= 5))
    strategies.append(("odds_le_10", "v26 1位 ∧ 単勝オッズ≤10", lambda r: r["win_odds"] is not None and float(r["win_odds"]) <= 10))
    strategies.append(("odds_ge_10", "v26 1位 ∧ 単勝オッズ≥10", lambda r: r["win_odds"] is not None and float(r["win_odds"]) >= 10))

    # DM フィルタ
    strategies.append(("dm_battle_top1", "v26 1位 ∧ DM対戦1位", lambda r: r["battle_dm_rank"] == 1))
    strategies.append(("dm_time_top1", "v26 1位 ∧ DMタイム1位", lambda r: r["time_dm_rank"] == 1))
    strategies.append(("dm_both_top1", "v26 1位 ∧ DM両方1位 (三冠)", lambda r: r["battle_dm_rank"] == 1 and r["time_dm_rank"] == 1))
    strategies.append(("dm_battle_ge65", "v26 1位 ∧ jvan_battle_dm≥65", lambda r: r["jvan_battle_dm"] is not None and float(r["jvan_battle_dm"]) >= 65))

    # anagusa
    strategies.append(("anagusa_AB", "v26 1位 ∧ anagusa A/B", lambda r: r["anagusa_rank"] in ("A", "B")))
    strategies.append(("anagusa_A", "v26 1位 ∧ anagusa A", lambda r: r["anagusa_rank"] == "A"))

    # 外部指数
    strategies.append(("nb_course_top1", "v26 1位 ∧ netkeiba(course)1位", lambda r: r["nb_course_rank"] == 1))
    strategies.append(("km_top1", "v26 1位 ∧ kichiuma 1位", lambda r: r["km_rank"] == 1))
    strategies.append(("ext_consensus", "v26 1位 ∧ netkeiba+kichiuma両方1位", lambda r: r["nb_course_rank"] == 1 and r["km_rank"] == 1))
    strategies.append(("ext_any_top1", "v26 1位 ∧ netkeiba or kichiuma 1位", lambda r: r["nb_course_rank"] == 1 or r["km_rank"] == 1))

    # 組合せ (高確度)
    strategies.append(("triple_consensus", "v26 1位 ∧ DM両方1位 ∧ ext両方1位", lambda r: r["battle_dm_rank"] == 1 and r["time_dm_rank"] == 1 and r["nb_course_rank"] == 1 and r["km_rank"] == 1))
    strategies.append(("safe_pop1_dm", "v26 1位 ∧ 1番人気 ∧ DM対戦1位", lambda r: r["win_popularity"] == 1 and r["battle_dm_rank"] == 1))

    # 大穴系
    strategies.append(("upset_anagusa_pop_ge5", "v26 1位 ∧ anagusa A/B ∧ 人気≥5", lambda r: r["anagusa_rank"] in ("A", "B") and r["win_popularity"] is not None and r["win_popularity"] >= 5))
    strategies.append(("upset_dm_battle_pop_ge6", "v26 1位 ∧ DM対戦1位 ∧ 人気≥6", lambda r: r["battle_dm_rank"] == 1 and r["win_popularity"] is not None and r["win_popularity"] >= 6))

    # 見送り系（reverse - 当てたくない条件）
    strategies.append(("skip_pop_ge8", "v26 1位 ∧ 人気≥8 (大穴の指数1位、危険)", lambda r: r["win_popularity"] is not None and r["win_popularity"] >= 8))

    # 評価
    print(f"\n=== {args.start}〜{args.end} 購入戦略シミュレーション ===\n")
    print(f"{'戦略':<45}{'n':>6}{'勝率':>9}{'複勝率':>10}{'単ROI':>10}{'複ROI':>10}")
    print("-" * 92)
    results = []
    for name, desc, pred in strategies:
        m = evaluate_strategy(rows, name, desc, pred)
        results.append(m)
        if m["n"] == 0:
            print(f"{desc:<45}{'0':>6}{'—':>9}{'—':>10}{'—':>10}{'—':>10}")
            continue
        print(
            f"{desc:<45}{m['n']:>6}"
            f"{fmt_pct(m['win_pct']):>9}{fmt_pct(m['place_pct']):>10}"
            f"{m['win_roi']:>9.3f}{m['place_roi']:>10.3f}"
        )

    # ROI > 1.0 の戦略をハイライト
    print("\n=== ROI ≥ 1.0 の購入条件 (期待値プラス) ===\n")
    positive = [m for m in results if m["n"] >= 30 and (m.get("win_roi", 0) >= 1.0 or m.get("place_roi", 0) >= 1.0)]
    if not positive:
        print("  (該当なし)")
    else:
        for m in positive:
            kind = []
            if m.get("win_roi", 0) >= 1.0:
                kind.append(f"単勝ROI {m['win_roi']:.3f}")
            if m.get("place_roi", 0) >= 1.0:
                kind.append(f"複勝ROI {m['place_roi']:.3f}")
            print(f"  ✓ {m['desc']:<45} n={m['n']:>4}  {' / '.join(kind)}")


if __name__ == "__main__":
    main()
