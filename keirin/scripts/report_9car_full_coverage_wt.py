#!/usr/bin/env python3
"""9車を開催まるごと網羅したときの当たり／外れを picks_history から集計する（2026-08-25）。

母集団 = `#9C`（ゲート通過）∪ `#9F`（穴埋め）。両者は排他なので union が
「9車の全レース」になる。実際に入稿したかどうかは `netkeirin_submissions` で分ける。

🔴 `#9C` と `#9F` は**買い方が違う**（軸が p3上位2車 / ライン組み替え）。
   同じ表に並べるときは必ず経路を分けて見ること。

使い方:
    PYTHONPATH=. .venv/bin/python scripts/report_9car_full_coverage_wt.py \
        [--start 2024-01-01] [--end 2026-08-24] [--by rtype|nl|day|rno|venue]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BY_SQL = {
    "rtype": ("r.race_type", "種別"),
    "nl":    ("(SELECT max(e.n_lines) FROM keirin.wt_entries e WHERE e.race_key=r.race_key)", "ライン数"),
    "day":   ("r.day_index", "開催何日目"),
    "rno":   ("r.race_no", "レース番号"),
    "venue": ("r.venue_id", "場"),
    "grade": ("r.cup_grade", "開催グレード"),
    "month": ("left(r.race_date::text,7)", "月"),
}

BASE = """
WITH p AS (
  SELECT ph.rank, ph.race_date, ph.hit, ph.payout, ph.bet_amount, ph.n_combos,
         replace(replace(ph.race_key,'#9C',''),'#9F','') AS rk
  FROM keirin.picks_history ph
  WHERE ph.rank IN ('RANK_9C','RANK_9F') AND ph.race_date BETWEEN %(s)s AND %(e)s
), j AS (
  SELECT p.*, r.race_type, r.day_index, r.race_no, r.cup_grade, r.venue_id, r.race_key,
         (s.race_key IS NOT NULL) AS submitted, s.origin
  FROM p JOIN keirin.wt_races r ON r.race_key = p.rk
  LEFT JOIN keirin.netkeirin_submissions s
         ON s.race_key = p.rk AND s.rank_key = '9C'
)
"""


def run(cur, sql, params):
    cur.execute(sql, params)
    return cur.fetchall()


def show(rows, head):
    print("  " + " | ".join(head))
    for r in rows:
        cells = []
        for v in r:
            cells.append(f"{v}" if v is not None else "-")
        print("  " + " | ".join(cells))
    print()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-12-31")
    ap.add_argument("--by", default="rtype", choices=sorted(BY_SQL))
    ap.add_argument("--min-n", type=int, default=40)
    args = ap.parse_args()

    url = os.environ.get("KEIRIN_DB_URL")
    if not url:
        sys.exit("KEIRIN_DB_URL が未設定です（VPS PG の picks_history を読みます）")
    import psycopg2
    with psycopg2.connect(url) as c, c.cursor() as cur:
        prm = {"s": args.start, "e": args.end}

        print(f"=== 9車 全網羅（{args.start}〜{args.end}）===\n")
        print("■ 経路別")
        show(run(cur, BASE + """
          SELECT CASE rank WHEN 'RANK_9C' THEN '#9C ゲート通過'
                           ELSE '#9F 穴埋め' END,
                 count(*), round(100.0*avg(hit),1),
                 round(100.0*sum(payout)/nullif(sum(bet_amount),0),1),
                 round(avg(n_combos),2)
          FROM j GROUP BY 1 ORDER BY 1""", prm),
             ["経路", "R", "的中%", "ROI%", "平均点数"])

        # 🔴 #9C と #9F は排他でなければならない。両方立つのは
        #    「片方だけ別 vintage で作った」印（実測: 7月を m2606 で作って15件重複）。
        dup = run(cur, BASE + """
          SELECT count(*) FROM (SELECT rk FROM j GROUP BY rk HAVING count(*) > 1) t""", prm)[0][0]
        tot = run(cur, BASE + "SELECT count(DISTINCT rk) FROM j", prm)[0][0]
        all9 = run(cur, """
          SELECT count(*) FROM keirin.wt_races
          WHERE n_entries = 9 AND race_date BETWEEN %(s)s AND %(e)s""", prm)[0][0]
        print(f"■ 整合: 9車 {all9}R / 記録 {tot}R（網羅 {100*tot/all9 if all9 else 0:.1f}%）"
              f" / #9C と #9F の重複 {dup}R")
        if dup:
            print("  🔴 重複あり。9C と 9F を**同じ vintage 窓**で作り直すこと")
        print()

        print("■ 実入稿したか（記録の欠けを確認する）")
        show(run(cur, BASE + """
          SELECT CASE rank WHEN 'RANK_9C' THEN '#9C' ELSE '#9F' END,
                 submitted, count(*), round(100.0*avg(hit),1),
                 round(100.0*sum(payout)/nullif(sum(bet_amount),0),1)
          FROM j GROUP BY 1,2 ORDER BY 1,2""", prm),
             ["経路", "入稿済", "R", "的中%", "ROI%"])

        col, label = BY_SQL[args.by]
        print(f"■ {label}別（経路をまとめた全網羅）")
        show(run(cur, BASE + f"""
          SELECT {col.replace('r.race_key','j.race_key')}, count(*), round(100.0*avg(hit),1),
                 round(100.0*sum(payout)/nullif(sum(bet_amount),0),1)
          FROM j r GROUP BY 1 HAVING count(*) >= %(n)s ORDER BY 3 DESC""",
                     {**prm, "n": args.min_n}),
             [label, "R", "的中%", "ROI%"])


if __name__ == "__main__":
    main()
