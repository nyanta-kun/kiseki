"""スイートスポット該当馬がレース内に多数いる場合の除外効果を検証。

スイートスポット = 単勝≥10 ∧ 期待値 1.2-5.0 ∧ 何らかのバッジ。
レース内の該当頭数別に勝率・複勝率・単ROI・複ROI を比較し、
「該当≥N頭のレースを除外」した場合の累積ROIを評価する。
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root.parent / ".env")

import psycopg2

from src.indices.buy_signal import is_sweet_spot, jra_horse_purchase_signal
from src.indices.dm_signals import compute_dm_signals

_JRA_TO_SEKITO: dict[str, str] = {
    "01": "JSPK", "02": "JHKD", "03": "JFKS", "04": "JNGT", "05": "JTOK",
    "06": "JNKY", "07": "JCKO", "08": "JKYO", "09": "JHSN", "10": "JKKR",
}

QUERY = """
SELECT
    ci.race_id, ci.horse_id, ci.composite_index, ci.win_probability,
    re.horse_number, re.jvan_time_dm, re.jvan_battle_dm,
    rr.finish_position, rr.win_popularity, rr.win_odds, rr.place_odds,
    r.head_count, r.date AS race_date, r.course AS jra_course,
    r.race_number, r.course_name, r.surface, r.distance
FROM keiba.calculated_indices ci
JOIN keiba.race_results rr  ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
JOIN keiba.race_entries re  ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
JOIN keiba.races         r  ON r.id = ci.race_id
WHERE ci.version = 26
  AND r.head_count >= 8
  AND r.date BETWEEN %(start)s AND %(end)s
  AND COALESCE(rr.abnormality_code, 0) = 0
  AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
"""

EXT_QUERY = """
WITH netkeiba_ranks AS (
  SELECT n.date AS race_date, n.course_code AS sekito_course, n.race_no AS race_number, n.horse_no,
    RANK() OVER (PARTITION BY n.date, n.course_code, n.race_no
                 ORDER BY (CASE WHEN n.idx_course ~ '^-?[0-9]+(\\.[0-9]+)?$'
                                THEN n.idx_course::numeric ELSE NULL END) DESC NULLS LAST) AS nb_course_rank,
    RANK() OVER (PARTITION BY n.date, n.course_code, n.race_no
                 ORDER BY (CASE WHEN n.idx_ave ~ '^-?[0-9]+(\\.[0-9]+)?$'
                                THEN n.idx_ave::numeric ELSE NULL END) DESC NULLS LAST) AS nb_ave_rank
  FROM sekito.netkeiba n WHERE n.date BETWEEN %(date_start)s AND %(date_end)s
),
kichiuma_ranks AS (
  SELECT k.date AS race_date, k.course_code AS sekito_course, k.race_no AS race_number, k.horse_no,
    RANK() OVER (PARTITION BY k.date, k.course_code, k.race_no
                 ORDER BY k.sp_score DESC NULLS LAST) AS km_rank
  FROM sekito.kichiuma k WHERE k.date BETWEEN %(date_start)s AND %(date_end)s
)
SELECT COALESCE(n.race_date, k.race_date) AS race_date,
       COALESCE(n.sekito_course, k.sekito_course) AS sekito_course,
       COALESCE(n.race_number, k.race_number) AS race_number,
       COALESCE(n.horse_no, k.horse_no) AS horse_no,
       n.nb_course_rank, n.nb_ave_rank, k.km_rank
FROM netkeiba_ranks n
FULL OUTER JOIN kichiuma_ranks k
  ON k.race_date = n.race_date AND k.sekito_course = n.sekito_course
 AND k.race_number = n.race_number AND k.horse_no = n.horse_no
"""

ANAGUSA_QUERY = """
SELECT date, course_code, race_no, horse_no, rank
FROM sekito.anagusa
WHERE date BETWEEN %(date_start)s AND %(date_end)s
"""


class HorseObj:
    __slots__ = ("horse_number", "composite_index", "jvan_time_dm",
                 "jvan_battle_dm", "anagusa_rank", "dm_signals")

    def __init__(self, **kwargs):
        for k in self.__slots__:
            setattr(self, k, kwargs.get(k))
        self.dm_signals = []


def fmt_pct(v: float) -> str:
    return f"{v * 100:.2f}%"


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

    print(f"Loading horses...", file=sys.stderr)
    cur.execute(QUERY, {"start": args.start, "end": args.end})
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    print(f"  loaded {len(rows)} horses", file=sys.stderr)

    date_start = f"{args.start[:4]}-{args.start[4:6]}-{args.start[6:8]}"
    date_end = f"{args.end[:4]}-{args.end[4:6]}-{args.end[6:8]}"

    print("Loading external/anagusa...", file=sys.stderr)
    cur.execute(EXT_QUERY, {"date_start": date_start, "date_end": date_end})
    ext_cols = [d[0] for d in cur.description]
    ext_map: dict[tuple, dict] = {}
    for row in cur.fetchall():
        er = dict(zip(ext_cols, row))
        if er["race_date"] is None:
            continue
        key = (er["race_date"].strftime("%Y%m%d"),
               er["sekito_course"], er["race_number"], er["horse_no"])
        ext_map[key] = er

    cur.execute(ANAGUSA_QUERY, {"date_start": date_start, "date_end": date_end})
    ana_map: dict[tuple, str] = {}
    for d, cc, rn, hn, rank in cur.fetchall():
        ana_map[(d.strftime("%Y%m%d"), cc, rn, hn)] = rank

    cur.close()
    conn.close()

    for r in rows:
        sekito = _JRA_TO_SEKITO.get(r["jra_course"])
        key = (r["race_date"], sekito, r["race_number"], r["horse_number"]) if sekito else None
        ext = ext_map.get(key) if key else None
        r["nb_course_rank"] = ext["nb_course_rank"] if ext else None
        r["nb_ave_rank"] = ext["nb_ave_rank"] if ext else None
        r["km_rank"] = ext["km_rank"] if ext else None
        r["anagusa_rank"] = ana_map.get(key) if key else None
        wp, wo = r.get("win_probability"), r.get("win_odds")
        r["expected_value"] = (
            float(wp) * float(wo) if (wp is not None and wo is not None) else None
        )

    # レース単位で signals + sweet spot 判定
    by_race: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        by_race[r["race_id"]].append(r)

    print(f"Annotating {len(by_race)} races...", file=sys.stderr)
    for race_rows in by_race.values():
        horses = [
            HorseObj(
                horse_number=r["horse_number"],
                composite_index=float(r["composite_index"] or 0.0),
                jvan_time_dm=(float(r["jvan_time_dm"]) if r["jvan_time_dm"] is not None else None),
                jvan_battle_dm=(float(r["jvan_battle_dm"]) if r["jvan_battle_dm"] is not None else None),
                anagusa_rank=r["anagusa_rank"],
            )
            for r in race_rows
        ]
        pop_map = {r["horse_number"]: r["win_popularity"]
                   for r in race_rows if r["win_popularity"] is not None}
        odds_map = {r["horse_number"]: float(r["win_odds"])
                    for r in race_rows if r["win_odds"] is not None}
        first = race_rows[0]
        compute_dm_signals(
            horses, popularity_map=pop_map, win_odds_map=odds_map,
            course_name=first["course_name"],
            surface=first["surface"], distance=first["distance"],
        )
        sig_map = {h.horse_number: h.dm_signals for h in horses}

        sorted_h = sorted(race_rows, key=lambda x: -(x["composite_index"] or 0.0))
        ranks = {h["horse_number"]: i + 1 for i, h in enumerate(sorted_h)}
        comps = [float(h["composite_index"] or 0.0) for h in sorted_h]
        gap = comps[1] - comps[2] if len(comps) >= 3 else None

        for r in race_rows:
            r["dm_signals"] = sig_map.get(r["horse_number"], [])
            r["composite_rank"] = ranks[r["horse_number"]]
            r["purchase_signal"] = jra_horse_purchase_signal(
                rank=r["composite_rank"], top2_t3_gap=gap,
                win_odds=(float(r["win_odds"]) if r["win_odds"] is not None else None),
            )
            r["is_sweet_spot"] = is_sweet_spot(
                win_odds=(float(r["win_odds"]) if r["win_odds"] is not None else None),
                win_probability=(float(r["win_probability"]) if r["win_probability"] is not None else None),
                composite_rank=r["composite_rank"],
                dm_signals=r["dm_signals"],
                purchase_signal=r["purchase_signal"],
                anagusa_rank=r["anagusa_rank"],
                nb_course_rank=r["nb_course_rank"],
                nb_ave_rank=r["nb_ave_rank"],
                km_rank=r["km_rank"],
            )

    # レースごとにスイートスポット該当頭数を計算
    race_sweet_count: dict[int, int] = {
        rid: sum(1 for r in race_rows if r["is_sweet_spot"])
        for rid, race_rows in by_race.items()
    }
    # 各馬にレースの該当頭数を付与
    for rid, race_rows in by_race.items():
        cnt = race_sweet_count[rid]
        for r in race_rows:
            r["race_sweet_count"] = cnt

    sweet_picks = [r for r in rows if r["is_sweet_spot"]]
    print(f"\n対象スイートスポット馬: {len(sweet_picks):,}\n")

    # === 1) 該当頭数別の単独ROI ===
    print(f"=== 該当頭数 (k) 別 単複ROI ===\n")
    print(f"{'k':>3}{'レース数':>9}{'対象馬数':>9}{'勝率':>9}{'複勝率':>10}{'単ROI':>9}{'複ROI':>9}")
    print("-" * 60)
    bucket: dict[int, list[dict]] = defaultdict(list)
    for r in sweet_picks:
        bucket[r["race_sweet_count"]].append(r)

    for k in sorted(bucket.keys()):
        picks = bucket[k]
        n_races = len({r["race_id"] for r in picks})
        n = len(picks)
        win = sum(1 for r in picks if r["finish_position"] == 1)
        place = sum(1 for r in picks
                    if r["finish_position"] is not None and r["finish_position"] <= 3)
        win_returns = sum(float(r["win_odds"] or 0)
                          for r in picks if r["finish_position"] == 1)
        place_returns = sum(float(r["place_odds"] or 1.5)
                            for r in picks
                            if r["finish_position"] is not None and r["finish_position"] <= 3)
        print(
            f"{k:>3}{n_races:>9}{n:>9}"
            f"{fmt_pct(win/n):>9}{fmt_pct(place/n):>10}"
            f"{win_returns/n:>9.3f}{place_returns/n:>9.3f}"
        )

    # === 2) 「該当頭数 ≤ N の馬のみ買う」累積ROI ===
    print(f"\n=== 「該当頭数 ≤ N」フィルタ後 累積ROI ===\n")
    print(f"{'N':>3}{'レース数':>9}{'対象馬数':>9}{'年換算':>9}{'勝率':>9}{'単ROI':>9}{'複ROI':>9}")
    print("-" * 60)
    for n_threshold in [1, 2, 3, 4, 5, 10]:
        kept = [r for r in sweet_picks if r["race_sweet_count"] <= n_threshold]
        if not kept:
            continue
        n_races = len({r["race_id"] for r in kept})
        n = len(kept)
        win = sum(1 for r in kept if r["finish_position"] == 1)
        place = sum(1 for r in kept
                    if r["finish_position"] is not None and r["finish_position"] <= 3)
        win_returns = sum(float(r["win_odds"] or 0)
                          for r in kept if r["finish_position"] == 1)
        place_returns = sum(float(r["place_odds"] or 1.5)
                            for r in kept
                            if r["finish_position"] is not None and r["finish_position"] <= 3)
        per_year = n / 3.0
        print(
            f"{n_threshold:>3}{n_races:>9}{n:>9}{per_year:>9.0f}"
            f"{fmt_pct(win/n):>9}{win_returns/n:>9.3f}{place_returns/n:>9.3f}"
        )

    # 全該当（フィルタなし）
    n = len(sweet_picks)
    win = sum(1 for r in sweet_picks if r["finish_position"] == 1)
    place = sum(1 for r in sweet_picks
                if r["finish_position"] is not None and r["finish_position"] <= 3)
    win_returns = sum(float(r["win_odds"] or 0)
                      for r in sweet_picks if r["finish_position"] == 1)
    place_returns = sum(float(r["place_odds"] or 1.5)
                        for r in sweet_picks
                        if r["finish_position"] is not None and r["finish_position"] <= 3)
    n_races = len({r["race_id"] for r in sweet_picks})
    print(
        f"\n{'all':>3}{n_races:>9}{n:>9}{n/3:>9.0f}"
        f"{fmt_pct(win/n):>9}{win_returns/n:>9.3f}{place_returns/n:>9.3f}"
        f"  （ベースライン）"
    )

    # === 2.5) k=2 レースで指数上位/下位 単独評価 ===
    print(f"\n=== k=2 レース: 指数上位 vs 下位 単独購入 ===\n")
    print(f"{'戦略':<32}{'レース数':>9}{'馬数':>7}{'勝率':>9}{'複勝率':>10}{'単ROI':>9}{'複ROI':>9}")
    print("-" * 84)

    k2_races: dict[int, list[dict]] = defaultdict(list)
    for r in sweet_picks:
        if r["race_sweet_count"] == 2:
            k2_races[r["race_id"]].append(r)

    def _eval(label: str, picks: list[dict], n_races: int) -> None:
        n = len(picks)
        if n == 0:
            print(f"{label:<32}{n_races:>9}{n:>7}{'—':>9}{'—':>10}{'—':>9}{'—':>9}")
            return
        win = sum(1 for r in picks if r["finish_position"] == 1)
        place = sum(1 for r in picks
                    if r["finish_position"] is not None and r["finish_position"] <= 3)
        wr = sum(float(r["win_odds"] or 0)
                 for r in picks if r["finish_position"] == 1)
        pr = sum(float(r["place_odds"] or 1.5)
                 for r in picks
                 if r["finish_position"] is not None and r["finish_position"] <= 3)
        print(
            f"{label:<32}{n_races:>9}{n:>7}"
            f"{fmt_pct(win/n):>9}{fmt_pct(place/n):>10}"
            f"{wr/n:>9.3f}{pr/n:>9.3f}"
        )

    upper, lower, both = [], [], []
    for race_rows in k2_races.values():
        # composite_rank が小さい方が指数上位
        sorted_by_rank = sorted(race_rows, key=lambda x: x["composite_rank"])
        upper.append(sorted_by_rank[0])
        lower.append(sorted_by_rank[1])
        both.extend(race_rows)
    n_races_k2 = len(k2_races)

    _eval("k=2 両方買い (現状)", both, n_races_k2)
    _eval("k=2 指数上位のみ買い", upper, n_races_k2)
    _eval("k=2 指数下位のみ買い", lower, n_races_k2)

    # 該当頭数 ≤ N かつ k=2 のときは指数上位のみ採用 のシミュレーション
    print(f"\n=== 「k=2は指数上位のみ採用」運用シミュレーション ===\n")
    print(f"{'戦略':<32}{'レース数':>9}{'馬数':>7}{'年換算':>7}{'勝率':>9}{'単ROI':>9}{'複ROI':>9}")
    print("-" * 84)

    # 戦略A: 既存 (k≤10 = 全馬)
    _eval_with_year = lambda label, picks, races: (
        print(
            f"{label:<32}{races:>9}{len(picks):>7}{len(picks)/3:>7.0f}"
            f"{fmt_pct(sum(1 for r in picks if r['finish_position']==1)/max(1,len(picks))):>9}"
            f"{sum(float(r['win_odds'] or 0) for r in picks if r['finish_position']==1)/max(1,len(picks)):>9.3f}"
            f"{sum(float(r['place_odds'] or 1.5) for r in picks if r['finish_position'] is not None and r['finish_position']<=3)/max(1,len(picks)):>9.3f}"
        )
    )

    # 戦略B: k=1 + k=2(指数上位のみ)
    strat_b: list[dict] = []
    for r in sweet_picks:
        if r["race_sweet_count"] == 1:
            strat_b.append(r)
        elif r["race_sweet_count"] == 2:
            # k=2 のレースで指数上位のみ
            same_race = k2_races[r["race_id"]]
            top = min(same_race, key=lambda x: x["composite_rank"])
            if r["horse_number"] == top["horse_number"]:
                strat_b.append(r)
    races_b = len({r["race_id"] for r in strat_b})

    # 戦略C: k=1 + k=2(指数上位) + k=3,4...は除外
    # (k≥3を除外する k≤2 フィルタとの組合せ)
    strat_c: list[dict] = strat_b  # k≥3を除外なので strat_b と同じ

    # 戦略D: k≤2 全馬買い (k=2 両方買い)
    strat_d = [r for r in sweet_picks if r["race_sweet_count"] <= 2]
    races_d = len({r["race_id"] for r in strat_d})

    # 戦略E: 全馬 (ベースライン)
    races_e = len({r["race_id"] for r in sweet_picks})

    _eval_with_year("ベースライン (全馬)", sweet_picks, races_e)
    _eval_with_year("k≤2 両方買い", strat_d, races_d)
    _eval_with_year("k=1 + k=2上位のみ", strat_b, races_b)

    # === 3) レース該当頭数の分布 ===
    print(f"\n=== 該当頭数別 レース数分布 ===\n")
    race_count_dist: dict[int, int] = defaultdict(int)
    for cnt in race_sweet_count.values():
        race_count_dist[cnt] += 1
    total_races = len(race_sweet_count)
    print(f"{'k':>3}{'レース数':>9}{'割合':>9}{'累積':>9}")
    cum = 0
    for k in sorted(race_count_dist.keys()):
        cum += race_count_dist[k]
        print(f"{k:>3}{race_count_dist[k]:>9}"
              f"{fmt_pct(race_count_dist[k]/total_races):>9}"
              f"{fmt_pct(cum/total_races):>9}")


if __name__ == "__main__":
    main()
