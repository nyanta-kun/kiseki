"""WIN5 の「上位から順に何点買えば当たったか」を測る。

## この指標が答えるもの

**最小的中点数 = Π_i（そのレースの勝ち馬の指数順位）**

各レースで指数上位 k_i 頭を買う長方形フォーメーションは、
すべてのレースで `k_i >= 勝ち馬の指数順位` のときに的中する。
それを満たす最小の点数が Π（勝ち馬の指数順位）である。

「予算 B 円あれば当たっていたか」が1つの数で言えるので、
週ごとの比較にそのまま使える。**払戻データを必要としない**のが利点で、
WIN5 の WF レコードを取り込む前から測れる。

## 🔴 これは達成可能な的中率ではない — 後知恵の下限である

`k_i = 勝ち馬の順位` を選ぶには**どのレースを広げるべきかを事前に知っている**必要がある。
最小的中点数は「もし完璧に読めていたら最低いくら要ったか」であって、
前向きに買える買い方の成績ではない。**画面に出すときは必ず下限と明記すること。**

意味があるのは3つの数の**差**である:

  一律k頭        … 配分の知恵ゼロ。全レースを同じ幅で買う
  最適化配分     … win_probability から前向きに幅を決める（multi_race_formation）
  最小的中点数   … 後知恵の下限

「一律 → 最適化」で詰まった分が配分アルゴリズムの実際の働き、
「最適化 → 下限」に残っている分がまだ取れていない余地になる。

## 対象5レースについて

🔴 **過去の WIN5 対象5レースは DB に無い**（`keiba.races` に WIN5 の印は無く、
WF レコードが未取込のため）。したがって本スクリプトは既定で
**その開催日の最後の5レースを proxy 集合**として使う。
WF を取り込んだら `--race-ids` で実際の5レースを渡すこと。
proxy と実集合ではレースの格が違う（WIN5 は上位クラスに寄る）ため、
**proxy で出た数値を WIN5 の実績として報告してはいけない。**

## 使い方

    cd backend
    .venv/bin/python scripts/jra_win5_min_hit_points.py --start 20260815 --end 20260830
    .venv/bin/python scripts/jra_win5_min_hit_points.py --race-ids 101,102,103,104,105
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402

from src.betting.multi_race_formation import (  # noqa: E402
    RaceCandidates,
    evaluate_formation,
    optimize_formation,
)

LEGS = 5
UNIT_PRICE = 100

# 前向き記録（発走前スナップショット）だけを見る。
# DB の calculated_indices はバックフィルで上書きされた in-sample 値なので使わない。
PICKS_SQL = """
SELECT r.date, p.race_id, r.course_name, r.race_number,
       p.horse_number, p.horse_name, p.index_rank, p.win_probability,
       p.finish_position
FROM keiba.hit_tier_picks p
JOIN keiba.hit_tier_races r ON r.race_id = p.race_id
WHERE r.date BETWEEN %(start)s AND %(end)s
  AND r.settled_at IS NOT NULL
ORDER BY r.date, r.race_number, p.index_rank
"""


def _connect():
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


def fetch_days(conn, start: str, end: str) -> dict[str, dict[int, dict[str, Any]]]:
    """日付 → race_id → {メタ, 出走馬} を返す。"""
    days: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(PICKS_SQL, {"start": start, "end": end})
        for row in cur.fetchall():
            race = days[row["date"]].setdefault(
                row["race_id"],
                {
                    "race_id": row["race_id"],
                    "course_name": row["course_name"],
                    "race_number": row["race_number"],
                    "horses": [],
                },
            )
            race["horses"].append(dict(row))
    return days


def _winner_rank(race: dict[str, Any]) -> int | None:
    for h in race["horses"]:
        if h["finish_position"] == 1:
            return int(h["index_rank"]) if h["index_rank"] else None
    return None


def _candidates(race: dict[str, Any]) -> RaceCandidates | None:
    """win_probability を降順に並べて配分アルゴリズムへ渡す形にする。"""
    hs = [h for h in race["horses"] if h["win_probability"] is not None]
    if not hs:
        return None
    hs.sort(key=lambda h: float(h["win_probability"]), reverse=True)
    return RaceCandidates(
        race_id=race["race_id"],
        win_probs=[float(h["win_probability"]) for h in hs],
        horses=[h["horse_number"] for h in hs],
    )


def analyze_day(races: list[dict[str, Any]], budgets: list[int]) -> dict[str, Any] | None:
    """1日分（5レース）の 最小的中点数 / 一律k頭 / 最適化配分 を並べる。"""
    ranks = [_winner_rank(r) for r in races]
    if any(r is None for r in ranks):
        return None
    min_points = 1
    for r in ranks:
        min_points *= r

    cands = [_candidates(r) for r in races]
    if any(c is None for c in cands):
        return None

    out: dict[str, Any] = {
        "races": [f"{r['course_name']}{r['race_number']}R" for r in races],
        "winner_index_ranks": ranks,
        "min_hit_points": min_points,
        "min_hit_cost_yen": min_points * UNIT_PRICE,
        "by_budget": [],
    }
    for b in budgets:
        max_tickets = b // UNIT_PRICE
        # 一律 k 頭（予算に収まる最大の k）
        k = 1
        while (k + 1) ** LEGS <= max_tickets:
            k += 1
        uniform = evaluate_formation(cands, [k] * LEGS, budget_yen=b)
        uniform_hit = all(rk <= k for rk in ranks)
        # 最適化配分
        plan = optimize_formation(cands, budget_yen=b)
        picks = [a.picks for a in plan.allocations]
        opt_hit = all(rk <= p for rk, p in zip(ranks, picks))
        out["by_budget"].append({
            "budget_yen": b,
            "uniform_k": k,
            "uniform_tickets": uniform.total_tickets,
            "uniform_hit": uniform_hit,
            "opt_picks": picks,
            "opt_tickets": plan.total_tickets,
            "opt_hit": opt_hit,
            "opt_hit_probability": round(plan.hit_probability, 4),
            "min_points_within_budget": min_points <= max_tickets,
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default="20260815")
    ap.add_argument("--end", default="20991231")
    ap.add_argument("--budgets", default="10000,30000,100000,312500",
                    help="比較する予算（円・カンマ区切り）")
    ap.add_argument("--out", help="JSON 出力先")
    args = ap.parse_args()
    budgets = [int(x) for x in args.budgets.split(",")]

    with _connect() as conn:
        days = fetch_days(conn, args.start, args.end)
    if not days:
        print("対象データがありません（前向き記録 hit_tier_picks が必要）")
        return

    print(f"=== WIN5 最小的中点数  {args.start}〜{args.end} ===")
    print("⚠️ 対象5レースは **その日の最後の5レース**（proxy）。"
          "実際の WIN5 対象は WF 取込後に --race-ids で渡すこと")
    print("⚠️ 最小的中点数は**後知恵の下限**であって達成可能な的中率ではない\n")

    results = []
    for d in sorted(days):
        races = sorted(days[d].values(), key=lambda r: r["race_number"])[-LEGS:]
        if len(races) < LEGS:
            continue
        res = analyze_day(races, budgets)
        if res:
            res["date"] = d
            results.append(res)

    print(f"{'日付':>9} {'勝ち馬の指数順位':>20} {'最小的中点数':>12} {'金額':>11}")
    for r in results:
        print(f"{r['date']:>9} {str(r['winner_index_ranks']):>20} "
              f"{r['min_hit_points']:>12,} {r['min_hit_cost_yen']:>10,}円")

    print(f"\n=== 予算別: 3つの買い方が当たっていたか（n={len(results)}日）===")
    print(f"{'予算':>9} {'一律k頭':>16} {'最適化配分':>16} {'下限(後知恵)':>12}")
    for i, b in enumerate(budgets):
        u = sum(1 for r in results if r["by_budget"][i]["uniform_hit"])
        o = sum(1 for r in results if r["by_budget"][i]["opt_hit"])
        m = sum(1 for r in results if r["by_budget"][i]["min_points_within_budget"])
        k = results[0]["by_budget"][i]["uniform_k"] if results else 0
        n = len(results)
        print(f"{b:>8,}円 {f'{u}/{n} (k={k})':>16} {f'{o}/{n}':>16} {f'{m}/{n}':>12}")

    print("\n※ n が小さいうちは「当たった日数」を率として読まないこと。")
    print("  前向き記録が貯まるほどこの表の意味が固まる。")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w") as f:
            json.dump({"window": [args.start, args.end], "days": results},
                      f, ensure_ascii=False, indent=2, default=str)
        print(f"\n書き出し: {args.out}")


if __name__ == "__main__":
    main()
