"""指定日のスイートスポット該当馬を抽出する。

スイートスポット条件 (2026-05-03 検証):
  - 単勝オッズ ≥ 10.0
  - 期待値 (win_probability × win_odds) 1.2 〜 5.0
  - 何らかのバッジあり: DM signals / purchase_signal / 穴ぐさA/B/C / 外部指数穴馬

3年バックテスト実証 (n=4,983, 1,661/年): 単ROI 1.182 / 複ROI 0.836。
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

from src.indices.buy_signal import jra_horse_purchase_signal
from src.indices.dm_signals import compute_dm_signals

_JRA_TO_SEKITO: dict[str, str] = {
    "01": "JSPK", "02": "JHKD", "03": "JFKS", "04": "JNGT", "05": "JTOK",
    "06": "JNKY", "07": "JCKO", "08": "JKYO", "09": "JHSN", "10": "JKKR",
}

# 当日のレース・指数・最新オッズ
QUERY = """
WITH latest_odds AS (
  SELECT DISTINCT ON (oh.race_id, oh.combination)
    oh.race_id, oh.combination::int AS horse_number, oh.odds AS win_odds
  FROM keiba.odds_history oh
  WHERE oh.bet_type = 'win'
    AND oh.race_id IN (SELECT id FROM keiba.races WHERE date = %(date)s)
  ORDER BY oh.race_id, oh.combination, oh.fetched_at DESC
),
latest_place_odds AS (
  SELECT DISTINCT ON (oh.race_id, oh.combination)
    oh.race_id, oh.combination::int AS horse_number, oh.odds AS place_odds
  FROM keiba.odds_history oh
  WHERE oh.bet_type = 'place'
    AND oh.race_id IN (SELECT id FROM keiba.races WHERE date = %(date)s)
  ORDER BY oh.race_id, oh.combination, oh.fetched_at DESC
)
SELECT
    ci.race_id, ci.horse_id,
    ci.composite_index, ci.win_probability,
    re.horse_number, h.name AS horse_name,
    re.jvan_time_dm, re.jvan_battle_dm,
    lo.win_odds, lpo.place_odds,
    r.head_count, r.date AS race_date,
    r.course AS jra_course, r.race_number, r.course_name,
    r.surface, r.distance, r.race_name
FROM keiba.calculated_indices ci
JOIN keiba.race_entries re ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
JOIN keiba.horses        h  ON h.id = re.horse_id
JOIN keiba.races         r  ON r.id = ci.race_id
LEFT JOIN latest_odds lo
       ON lo.race_id = ci.race_id AND lo.horse_number = re.horse_number
LEFT JOIN latest_place_odds lpo
       ON lpo.race_id = ci.race_id AND lpo.horse_number = re.horse_number
WHERE ci.version = 26
  AND r.date = %(date)s
  AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
  AND ci.race_id IN (
      SELECT race_id FROM keiba.race_entries GROUP BY race_id HAVING COUNT(*) >= 8
  )
"""

ANAGUSA_QUERY = """
SELECT date, course_code, race_no, horse_no, rank
FROM sekito.anagusa
WHERE date = %(date)s
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
  FROM sekito.netkeiba n WHERE n.date = %(date_iso)s
),
kichiuma_ranks AS (
  SELECT k.date AS race_date, k.course_code AS sekito_course, k.race_no AS race_number, k.horse_no,
    RANK() OVER (PARTITION BY k.date, k.course_code, k.race_no
                 ORDER BY k.sp_score DESC NULLS LAST) AS km_rank
  FROM sekito.kichiuma k WHERE k.date = %(date_iso)s
)
SELECT COALESCE(n.race_date, k.race_date) AS race_date,
       COALESCE(n.sekito_course, k.sekito_course) AS sekito_course,
       COALESCE(n.race_number, k.race_number) AS race_number,
       COALESCE(n.horse_no, k.horse_no) AS horse_no,
       n.nb_course_rank, n.nb_ave_rank, k.km_rank
FROM netkeiba_ranks n
FULL OUTER JOIN kichiuma_ranks k
  ON k.race_date = n.race_date
 AND k.sekito_course = n.sekito_course
 AND k.race_number = n.race_number
 AND k.horse_no = n.horse_no
"""


class HorseObj:
    __slots__ = ("horse_number", "composite_index", "jvan_time_dm",
                 "jvan_battle_dm", "anagusa_rank", "dm_signals")

    def __init__(self, **kwargs):
        for k in self.__slots__:
            setattr(self, k, kwargs.get(k))
        self.dm_signals = []


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--date", default="20260503")
    p.add_argument("--min-odds", type=float, default=10.0)
    p.add_argument("--min-ev", type=float, default=1.2)
    p.add_argument("--max-ev", type=float, default=5.0)
    args = p.parse_args()

    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(QUERY, {"date": args.date})
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    date_iso = f"{args.date[:4]}-{args.date[4:6]}-{args.date[6:8]}"
    cur.execute(EXT_QUERY, {"date_iso": date_iso})
    ext_cols = [d[0] for d in cur.description]
    ext_map: dict[tuple, dict] = {}
    for row in cur.fetchall():
        er = dict(zip(ext_cols, row))
        if er["race_date"] is None:
            continue
        key = (er["race_date"].strftime("%Y%m%d"),
               er["sekito_course"], er["race_number"], er["horse_no"])
        ext_map[key] = er

    cur.execute(ANAGUSA_QUERY, {"date": date_iso})
    ana_map: dict[tuple, str] = {}
    for d, cc, rn, hn, rank in cur.fetchall():
        ana_map[(d.strftime("%Y%m%d"), cc, rn, hn)] = rank

    cur.close()
    conn.close()

    # 馬ごとに anagusa / 外部指数 / 期待値 を付与
    for r in rows:
        sekito_course = _JRA_TO_SEKITO.get(r["jra_course"])
        rdate = r["race_date"]
        date_str = rdate if isinstance(rdate, str) else rdate.strftime("%Y%m%d")
        r["_date_str"] = date_str
        key = (date_str, sekito_course, r["race_number"], r["horse_number"]) if sekito_course else None
        ext = ext_map.get(key) if key else None
        r["nb_course_rank"] = ext["nb_course_rank"] if ext else None
        r["nb_ave_rank"] = ext["nb_ave_rank"] if ext else None
        r["km_rank"] = ext["km_rank"] if ext else None
        r["anagusa_rank"] = ana_map.get(key) if key else None
        wp = r.get("win_probability")
        wo = r.get("win_odds")
        r["expected_value"] = (
            float(wp) * float(wo) if (wp is not None and wo is not None) else None
        )

    # レース単位で DM signals + composite rank を計算
    by_race: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        by_race[r["race_id"]].append(r)

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
        pop_map: dict[int, int] = {}  # 当日は人気不明なのでオッズから推定
        odds_map = {
            r["horse_number"]: float(r["win_odds"])
            for r in race_rows if r["win_odds"] is not None
        }
        # オッズ → 暫定人気
        for hn, _o in sorted(odds_map.items(), key=lambda x: x[1]):
            pop_map[hn] = len(pop_map) + 1

        first = race_rows[0]
        compute_dm_signals(
            horses, popularity_map=pop_map, win_odds_map=odds_map,
            course_name=first["course_name"],
            surface=first["surface"], distance=first["distance"],
        )
        sig_map = {h.horse_number: h.dm_signals for h in horses}

        # composite rank + top2_t3_gap
        sorted_h = sorted(race_rows, key=lambda x: -(x["composite_index"] or 0.0))
        ranks = {h["horse_number"]: i + 1 for i, h in enumerate(sorted_h)}
        comps = [float(h["composite_index"] or 0.0) for h in sorted_h]
        gap = comps[1] - comps[2] if len(comps) >= 3 else None

        for r in race_rows:
            r["dm_signals"] = sig_map.get(r["horse_number"], [])
            r["composite_rank"] = ranks[r["horse_number"]]
            r["popularity"] = pop_map.get(r["horse_number"])
            r["purchase_signal"] = jra_horse_purchase_signal(
                rank=r["composite_rank"],
                top2_t3_gap=gap,
                win_odds=(float(r["win_odds"]) if r["win_odds"] is not None else None),
            )

    # スイートスポット判定
    def has_anagusa_badge(r: dict) -> bool:
        return r["anagusa_rank"] in ("A", "B", "C") and r["composite_rank"] >= 2

    def has_ext_dark(r: dict) -> bool:
        if r["composite_rank"] < 4:
            return False
        if r["nb_course_rank"] == 1:
            return True
        if r["nb_ave_rank"] is not None and r["nb_ave_rank"] <= 2 and r["km_rank"] == 1:
            return True
        return False

    def badges_of(r: dict) -> list[str]:
        b = []
        if r["dm_signals"]:
            b.extend(r["dm_signals"])
        if r["purchase_signal"] in ("super_buy", "buy", "watch"):
            b.append({"super_buy": "🔥超推奨", "buy": "◎推奨", "watch": "○注目"}[r["purchase_signal"]])
        if has_anagusa_badge(r):
            b.append(f"穴{r['anagusa_rank']}")
        if has_ext_dark(r):
            b.append("外部穴")
        return b

    picks: list[dict] = []
    for r in rows:
        if r["win_odds"] is None or float(r["win_odds"]) < args.min_odds:
            continue
        ev = r["expected_value"]
        if ev is None or ev < args.min_ev or ev > args.max_ev:
            continue
        b = badges_of(r)
        if not b:
            continue
        r["badges"] = b
        picks.append(r)

    # 出力 (course_name, race_number, ev降順)
    picks.sort(key=lambda x: (x["course_name"], x["race_number"], -x["expected_value"]))
    print(f"\n=== {args.date} スイートスポット該当馬 (単勝≥{args.min_odds} ∧ 期待値 {args.min_ev}-{args.max_ev} ∧ バッジあり) ===\n")
    print(f"{'場':<3}{'R':>3} {'馬番':>3} {'馬名':<14} {'人':>3} {'単':>7} {'p':>6} {'EV':>5} {'CI順':>4} バッジ")
    print("-" * 100)
    last_race = None
    for r in picks:
        race_key = (r["course_name"], r["race_number"], r.get("race_name") or "")
        if race_key != last_race:
            if last_race is not None:
                print()
            print(f"  ▼ {r['course_name']} {r['race_number']}R "
                  f"{r.get('race_name') or ''}")
            last_race = race_key
        print(
            f"{'':3}{'':3} {r['horse_number']:>3} {(r['horse_name'] or '')[:14]:<14}"
            f" {r.get('popularity') or '?':>3}"
            f" {float(r['win_odds']):>7.1f}"
            f" {float(r['win_probability']) * 100:>5.1f}%"
            f" {r['expected_value']:>5.2f}"
            f" {r['composite_rank']:>4}"
            f" {' / '.join(r['badges'])}"
        )

    print(f"\n該当馬: {len(picks)} 頭 (全 {len(rows)} 頭中)\n")


if __name__ == "__main__":
    main()
