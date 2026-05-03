"""バッジあり馬の単複ROI集計（単勝10倍以上）。

「v26 1位馬」に絞らず、レース内の全馬について以下のバッジ条件を集計する:
  - DM signals (7種: 三冠一致 / 高得点鉄板 / 穴ぐさDM / DM大穴 / DM高オッズ / 穴ぐさ+DMtime / 人気下振れ)
  - purchase_signal (super_buy / buy / watch — buy_signal.py)
  - anagusa A/B/C (sekito.anagusa)
  - 外部指数穴馬 (CI4位以下 + nb_course_rank=1 / km_rank=1)

各バッジ単独 / any-badge の n / 勝率 / 複勝率 / 単ROI / 複ROI を出力する。
共通フィルタ: 単勝オッズ ≥ 10。
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
from src.indices.dm_signals import (
    SIGNAL_ANAGUSA_DM,
    SIGNAL_ANAGUSA_DM_TIME,
    SIGNAL_DM_BIG_DARK,
    SIGNAL_DM_HIGH_ODDS,
    SIGNAL_POPULAR_DOWNSIDE,
    SIGNAL_TOP_PREMIUM,
    SIGNAL_TRIPLE_MATCH,
    compute_dm_signals,
)

_JRA_TO_SEKITO: dict[str, str] = {
    "01": "JSPK", "02": "JHKD", "03": "JFKS", "04": "JNGT", "05": "JTOK",
    "06": "JNKY", "07": "JCKO", "08": "JKYO", "09": "JHSN", "10": "JKKR",
}

# レース内全馬の指数 + 結果 + オッズ を取得
QUERY = """
SELECT
    ci.race_id,
    ci.horse_id,
    ci.composite_index,
    ci.win_probability,
    re.horse_number,
    re.jvan_time_dm,
    re.jvan_battle_dm,
    rr.finish_position,
    rr.win_popularity,
    rr.win_odds,
    rr.place_odds,
    r.head_count,
    r.date            AS race_date,
    r.course          AS jra_course,
    r.race_number,
    r.course_name,
    r.surface,
    r.distance
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
  FROM sekito.netkeiba n
  WHERE n.date BETWEEN %(date_start)s AND %(date_end)s
),
kichiuma_ranks AS (
  SELECT k.date AS race_date, k.course_code AS sekito_course, k.race_no AS race_number, k.horse_no,
    RANK() OVER (PARTITION BY k.date, k.course_code, k.race_no
                 ORDER BY k.sp_score DESC NULLS LAST) AS km_rank
  FROM sekito.kichiuma k
  WHERE k.date BETWEEN %(date_start)s AND %(date_end)s
)
SELECT
  COALESCE(n.race_date, k.race_date)         AS race_date,
  COALESCE(n.sekito_course, k.sekito_course) AS sekito_course,
  COALESCE(n.race_number, k.race_number)     AS race_number,
  COALESCE(n.horse_no, k.horse_no)           AS horse_no,
  n.nb_course_rank, n.nb_ave_rank, k.km_rank
FROM netkeiba_ranks n
FULL OUTER JOIN kichiuma_ranks k
  ON k.race_date = n.race_date
 AND k.sekito_course = n.sekito_course
 AND k.race_number = n.race_number
 AND k.horse_no = n.horse_no
"""

ANAGUSA_QUERY = """
SELECT date, course_code, race_no, horse_no, rank
FROM sekito.anagusa
WHERE date BETWEEN %(date_start)s AND %(date_end)s
"""


class Horse:
    """compute_dm_signals に渡す簡易オブジェクト。"""

    __slots__ = (
        "horse_number",
        "composite_index",
        "jvan_time_dm",
        "jvan_battle_dm",
        "anagusa_rank",
        "dm_signals",
    )

    def __init__(
        self,
        horse_number: int,
        composite_index: float,
        jvan_time_dm: float | None,
        jvan_battle_dm: float | None,
        anagusa_rank: str | None,
    ):
        self.horse_number = horse_number
        self.composite_index = composite_index
        self.jvan_time_dm = jvan_time_dm
        self.jvan_battle_dm = jvan_battle_dm
        self.anagusa_rank = anagusa_rank
        self.dm_signals: list[str] = []


def fmt_pct(v: float) -> str:
    return f"{v * 100:.2f}%"


def load_data(start: str, end: str) -> list[dict]:
    dsn = (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')} "
        f"dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')} "
        f"password={os.getenv('DB_PASSWORD')}"
    )
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()

    print(f"Loading horses for {start}-{end}...", file=sys.stderr)
    cur.execute(QUERY, {"start": start, "end": end})
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    print(f"  loaded {len(rows)} horse records", file=sys.stderr)

    date_start = f"{start[:4]}-{start[4:6]}-{start[6:8]}"
    date_end = f"{end[:4]}-{end[4:6]}-{end[6:8]}"

    print("Loading external ranks...", file=sys.stderr)
    cur.execute(EXT_QUERY, {"date_start": date_start, "date_end": date_end})
    ext_cols = [d[0] for d in cur.description]
    ext_map: dict[tuple, dict] = {}
    for row in cur.fetchall():
        er = dict(zip(ext_cols, row))
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
    ana_map: dict[tuple, str] = {}
    for d, cc, rn, hn, rank in cur.fetchall():
        ana_map[(d.strftime("%Y%m%d"), cc, rn, hn)] = rank
    print(f"  loaded {len(ana_map)} anagusa picks", file=sys.stderr)

    cur.close()
    conn.close()

    # 各馬に外部指数 + anagusa を付与
    for r in rows:
        sekito_course = _JRA_TO_SEKITO.get(r["jra_course"])
        if sekito_course is None:
            r["nb_course_rank"] = r["nb_ave_rank"] = r["km_rank"] = None
            r["anagusa_rank"] = None
            continue
        key = (r["race_date"], sekito_course, r["race_number"], r["horse_number"])
        ext = ext_map.get(key)
        r["nb_course_rank"] = ext["nb_course_rank"] if ext else None
        r["nb_ave_rank"] = ext["nb_ave_rank"] if ext else None
        r["km_rank"] = ext["km_rank"] if ext else None
        r["anagusa_rank"] = ana_map.get(key)

    return rows


def annotate_signals(rows: list[dict]) -> list[dict]:
    """レース単位で DM signals + purchase_signal + composite_rank を付与する。"""
    by_race: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        by_race[r["race_id"]].append(r)

    print(f"Annotating {len(by_race)} races...", file=sys.stderr)
    for _race_id, race_rows in by_race.items():
        # DM signals
        horses = [
            Horse(
                horse_number=r["horse_number"],
                composite_index=float(r["composite_index"] or 0.0),
                jvan_time_dm=(float(r["jvan_time_dm"]) if r["jvan_time_dm"] is not None else None),
                jvan_battle_dm=(float(r["jvan_battle_dm"]) if r["jvan_battle_dm"] is not None else None),
                anagusa_rank=r["anagusa_rank"],
            )
            for r in race_rows
        ]
        pop_map = {
            r["horse_number"]: r["win_popularity"]
            for r in race_rows
            if r["win_popularity"] is not None
        }
        odds_map = {
            r["horse_number"]: float(r["win_odds"])
            for r in race_rows
            if r["win_odds"] is not None
        }
        first = race_rows[0]
        compute_dm_signals(
            horses,
            popularity_map=pop_map,
            win_odds_map=odds_map,
            course_name=first.get("course_name"),
            surface=first.get("surface"),
            distance=first.get("distance"),
        )
        sig_by_hn = {h.horse_number: h.dm_signals for h in horses}
        for r in race_rows:
            r["dm_signals"] = sig_by_hn.get(r["horse_number"], [])

        # composite_index 内ランク + 2位差 (top2_t3_gap)
        sorted_horses = sorted(race_rows, key=lambda x: -(x["composite_index"] or 0.0))
        ranks: dict[int, int] = {}
        for i, rr_ in enumerate(sorted_horses, start=1):
            ranks[rr_["horse_number"]] = i
        comp_sorted = [float(h["composite_index"] or 0.0) for h in sorted_horses]
        # top2_t3_gap = 2位 composite − 3位 composite
        top2_t3_gap = (
            comp_sorted[1] - comp_sorted[2] if len(comp_sorted) >= 3 else None
        )
        for r in race_rows:
            r["composite_rank"] = ranks[r["horse_number"]]
            r["top2_t3_gap"] = top2_t3_gap
            r["purchase_signal"] = jra_horse_purchase_signal(
                rank=r["composite_rank"],
                top2_t3_gap=top2_t3_gap,
                win_odds=(float(r["win_odds"]) if r["win_odds"] is not None else None),
            )

    return rows


def has_anagusa_badge(r: dict) -> bool:
    """穴ぐさバッジ: anagusa A/B/C ピックあり ∧ 1位以外。"""
    return r["anagusa_rank"] in ("A", "B", "C") and r["composite_rank"] >= 2


def has_ext_dark_badge(r: dict) -> bool:
    """外部指数穴馬: 4位以下 ∧ (NB course=1 or (NB ave≤2 ∧ KM=1))。"""
    if r["composite_rank"] < 4:
        return False
    if r["nb_course_rank"] == 1:
        return True
    if (r["nb_ave_rank"] is not None and r["nb_ave_rank"] <= 2 and r["km_rank"] == 1):
        return True
    return False


def has_dm_badge(r: dict) -> bool:
    return bool(r["dm_signals"])


def has_purchase_badge(r: dict) -> bool:
    return r["purchase_signal"] in ("super_buy", "buy", "watch")


def has_any_badge(r: dict) -> bool:
    return (
        has_anagusa_badge(r)
        or has_ext_dark_badge(r)
        or has_dm_badge(r)
        or has_purchase_badge(r)
    )


def evaluate(rows: list[dict], desc: str, pred) -> dict:
    matched = [r for r in rows if pred(r)]
    n = len(matched)
    if n == 0:
        return {"desc": desc, "n": 0}
    win = sum(1 for r in matched if r["finish_position"] == 1)
    place = sum(
        1 for r in matched if r["finish_position"] is not None and r["finish_position"] <= 3
    )
    win_returns = sum(
        float(r["win_odds"] or 0) for r in matched if r["finish_position"] == 1
    )
    place_returns = sum(
        float(r["place_odds"] or 1.5) for r in matched
        if r["finish_position"] is not None and r["finish_position"] <= 3
    )
    return {
        "desc": desc,
        "n": n,
        "win_pct": win / n,
        "place_pct": place / n,
        "win_roi": win_returns / n,
        "place_roi": place_returns / n,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20230501")
    p.add_argument("--end", default="20260501")
    p.add_argument("--min-odds", type=float, default=10.0,
                   help="単勝オッズ下限 (default: 10.0)")
    p.add_argument("--min-ev", type=float, default=None,
                   help="期待値 (win_probability × win_odds) 下限")
    p.add_argument("--max-ev", type=float, default=None,
                   help="期待値上限 (大穴外れ値除外用)")
    args = p.parse_args()

    rows = load_data(args.start, args.end)
    annotate_signals(rows)

    # 期待値を全馬に付与
    for r in rows:
        wp = r.get("win_probability")
        wo = r.get("win_odds")
        r["expected_value"] = (
            float(wp) * float(wo) if (wp is not None and wo is not None) else None
        )

    # 単勝オッズ ≥ min-odds + 期待値 [min-ev, max-ev] でフィルタ
    def ev_ok(r: dict) -> bool:
        ev = r["expected_value"]
        if args.min_ev is not None:
            if ev is None or ev < args.min_ev:
                return False
        if args.max_ev is not None:
            if ev is None or ev > args.max_ev:
                return False
        return True

    target = [
        r for r in rows
        if r["win_odds"] is not None and float(r["win_odds"]) >= args.min_odds
        and ev_ok(r)
    ]
    ev_label_parts = []
    if args.min_ev is not None:
        ev_label_parts.append(f"期待値≥{args.min_ev}")
    if args.max_ev is not None:
        ev_label_parts.append(f"期待値≤{args.max_ev}")
    ev_label = (" ∧ " + " ∧ ".join(ev_label_parts)) if ev_label_parts else ""
    print(
        f"\n単勝≥{args.min_odds}{ev_label} の馬: {len(target):,} / 全 {len(rows):,} 馬\n",
        file=sys.stderr,
    )

    print(f"\n=== {args.start}〜{args.end} 単勝≥{args.min_odds}{ev_label} ∧ バッジ別 単複ROI ===\n")
    print(f"{'戦略':<48}{'n':>7}{'勝率':>9}{'複勝率':>10}{'単ROI':>9}{'複ROI':>9}")
    print("-" * 92)

    strategies = [
        ("baseline (単勝≥10 全馬)", lambda r: True),
        ("単勝≥10 ∧ 何らかのバッジあり", has_any_badge),
        ("単勝≥10 ∧ DMシグナル (any)", has_dm_badge),
        ("単勝≥10 ∧ purchase_signal (super_buy/buy/watch)", has_purchase_badge),
        ("単勝≥10 ∧ 穴ぐさ A/B/C (1位以外)", has_anagusa_badge),
        ("単勝≥10 ∧ 外部指数穴馬", has_ext_dark_badge),
        # DM signals 個別
        ("単勝≥10 ∧ 🔥三冠一致", lambda r: SIGNAL_TRIPLE_MATCH in r["dm_signals"]),
        ("単勝≥10 ∧ ⭐高得点鉄板", lambda r: SIGNAL_TOP_PREMIUM in r["dm_signals"]),
        ("単勝≥10 ∧ 🏆穴ぐさDM", lambda r: SIGNAL_ANAGUSA_DM in r["dm_signals"]),
        ("単勝≥10 ∧ ⚡DM大穴", lambda r: SIGNAL_DM_BIG_DARK in r["dm_signals"]),
        ("単勝≥10 ∧ ⚡DM高オッズ", lambda r: SIGNAL_DM_HIGH_ODDS in r["dm_signals"]),
        ("単勝≥10 ∧ 💎穴ぐさ+DMtime", lambda r: SIGNAL_ANAGUSA_DM_TIME in r["dm_signals"]),
        ("単勝≥10 ∧ ❌人気下振れ (除外推奨)", lambda r: SIGNAL_POPULAR_DOWNSIDE in r["dm_signals"]),
        # purchase_signal 個別
        ("単勝≥10 ∧ super_buy", lambda r: r["purchase_signal"] == "super_buy"),
        ("単勝≥10 ∧ buy", lambda r: r["purchase_signal"] == "buy"),
        ("単勝≥10 ∧ watch", lambda r: r["purchase_signal"] == "watch"),
        # 組合せ
        ("単勝≥10 ∧ DM ∧ purchase",
            lambda r: has_dm_badge(r) and has_purchase_badge(r)),
        ("単勝≥10 ∧ DM ∧ 穴ぐさA/B/C",
            lambda r: has_dm_badge(r) and has_anagusa_badge(r)),
    ]

    for desc, pred in strategies:
        m = evaluate(target, desc, pred)
        if m["n"] == 0:
            print(f"{desc:<48}{'0':>7}{'—':>9}{'—':>10}{'—':>9}{'—':>9}")
            continue
        print(
            f"{desc:<48}{m['n']:>7}"
            f"{fmt_pct(m['win_pct']):>9}{fmt_pct(m['place_pct']):>10}"
            f"{m['win_roi']:>8.3f}{m['place_roi']:>9.3f}"
        )


if __name__ == "__main__":
    main()
