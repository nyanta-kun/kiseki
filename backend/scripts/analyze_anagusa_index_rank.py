"""穴ぐさ × 自指数順位 複勝ROI 分析

「穴ぐさA/B/C × v26指数上位（1〜5位）」での複勝的中率・ROIを検証。
DM指数との組み合わせも評価。

使い方:
  .venv/bin/python scripts/analyze_anagusa_index_rank.py [--start YYYYMMDD] [--end YYYYMMDD]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv
load_dotenv(_root.parent / ".env")

from src.db.session import sync_engine as engine

COURSE_MAP_SQL = """
  CASE a.course_code
    WHEN 'JSPK' THEN '01' WHEN 'JHKD' THEN '02' WHEN 'JFKS' THEN '03'
    WHEN 'JNGT' THEN '04' WHEN 'JTOK' THEN '05' WHEN 'JNKY' THEN '06'
    WHEN 'JCKO' THEN '07' WHEN 'JKYO' THEN '08' WHEN 'JHSN' THEN '09'
    WHEN 'JKKR' THEN '10'
  END
"""

QUERY = text(f"""
SELECT
    r.id              AS race_id,
    r.date            AS date,
    r.surface         AS surface,
    r.distance        AS distance,
    r.head_count      AS head_count,
    ci.horse_id,
    re.horse_number,
    ci.composite_index,
    re.jvan_time_dm,
    re.jvan_battle_dm,
    rr.finish_position,
    rr.abnormality_code,
    rr.win_odds,
    rr.place_odds,
    rr.win_popularity,
    a.rank            AS anagusa_rank
FROM keiba.calculated_indices ci
JOIN keiba.races r           ON r.id = ci.race_id
JOIN keiba.race_entries re   ON re.race_id = ci.race_id AND re.horse_id = ci.horse_id
JOIN keiba.race_results rr   ON rr.race_id = ci.race_id AND rr.horse_id = ci.horse_id
LEFT JOIN sekito.anagusa a   ON a.date = r.date::date
                             AND {COURSE_MAP_SQL} = r.course
                             AND a.race_no   = r.race_number
                             AND a.horse_no  = re.horse_number
WHERE r.date BETWEEN :start_date AND :end_date
  AND r.course IN ('01','02','03','04','05','06','07','08','09','10')
  AND ci.version = (SELECT MAX(version) FROM keiba.calculated_indices)
  AND COALESCE(rr.abnormality_code, 0) = 0
  AND rr.finish_position IS NOT NULL
  AND r.head_count >= 8
ORDER BY r.date, r.id, re.horse_number
""")


def load_data(start: str, end: str) -> pd.DataFrame:
    with Session(engine) as db:
        rows = db.execute(QUERY, {"start_date": start, "end_date": end}).fetchall()
    cols = [
        "race_id", "date", "surface", "distance", "head_count",
        "horse_id", "horse_number",
        "composite_index", "jvan_time_dm", "jvan_battle_dm",
        "finish_position", "abnormality_code", "win_odds", "place_odds",
        "win_popularity", "anagusa_rank",
    ]
    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return df
    for c in ["composite_index", "jvan_time_dm", "jvan_battle_dm",
              "finish_position", "win_odds", "place_odds", "win_popularity", "head_count"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # レース内ランク付与（1=最良）
    df["base_rank"] = df.groupby("race_id")["composite_index"].rank(
        method="min", ascending=False
    )
    df["time_rank"] = df.groupby("race_id")["jvan_time_dm"].rank(
        method="min", ascending=False
    )
    df["battle_rank"] = df.groupby("race_id")["jvan_battle_dm"].rank(
        method="min", ascending=False
    )

    n_races = df["race_id"].nunique()
    n_horses = len(df)
    print(f"対象: {n_races:,} レース / {n_horses:,} 馬")
    return df


def evaluate(df_sel: pd.DataFrame, label: str) -> dict:
    """単勝・複勝の的中率/ROI を計算。"""
    v = df_sel[df_sel["win_odds"].notna() & (df_sel["win_odds"] > 0)].copy()
    n = len(v)
    if n == 0:
        return {"label": label, "n": 0,
                "win_rate": "-", "place_rate": "-",
                "win_roi": "-", "place_roi": "-", "avg_pop": "-", "avg_odds": "-"}
    wins   = int((v["finish_position"] == 1).sum())
    places = int((v["finish_position"] <= 3).sum())
    win_roi = v.loc[v["finish_position"] == 1, "win_odds"].sum() / n
    avg_pop = v["win_popularity"].mean()
    avg_odds = v["win_odds"].mean()

    # 複勝ROI: place_oddsは3着以内の馬にしか格納されていないため、
    # 分母は全購入対象馬数(n)、分子は3着以内の払戻合計
    places_with_odds = v[(v["place_odds"].notna()) & (v["place_odds"] > 0) & (v["finish_position"] <= 3)]
    place_count = int((v["finish_position"] <= 3).sum())
    place_rate = place_count / n * 100
    place_rate_str = f"{place_rate:.1f}%"
    n_place = len(places_with_odds)
    if n_place > 0:
        place_roi = places_with_odds["place_odds"].sum() / n
        place_roi_str = f"{place_roi:.3f}"
    else:
        place_roi_str = "n/a"

    return {
        "label": label,
        "n": n,
        "n_place": n_place,
        "win_rate": f"{wins/n*100:.1f}%",
        "place_rate": place_rate_str,
        "win_roi": f"{win_roi:.3f}",
        "place_roi": place_roi_str,
        "avg_pop": f"{avg_pop:.1f}",
        "avg_odds": f"{avg_odds:.1f}",
    }


def print_table(rows: list[dict], title: str) -> None:
    print(f"\n{'='*80}")
    print(f"  {title}")
    print(f"{'='*80}")
    df = pd.DataFrame(rows)
    if df.empty or df["n"].eq(0).all():
        print("  (データなし)")
        return
    cols = ["label", "n", "win_rate", "place_rate", "win_roi", "place_roi", "avg_pop", "avg_odds"]
    df = df[[c for c in cols if c in df.columns]]
    print(df.to_string(index=False))


def run(df: pd.DataFrame) -> None:
    ana_a   = df["anagusa_rank"] == "A"
    ana_b   = df["anagusa_rank"] == "B"
    ana_c   = df["anagusa_rank"] == "C"
    ana_ab  = df["anagusa_rank"].isin(["A", "B"])
    ana_abc = df["anagusa_rank"].isin(["A", "B", "C"])
    unpop   = df["win_popularity"] >= 4   # 4番人気以下

    # =========================================================
    # 1. ベースライン
    # =========================================================
    base = [
        evaluate(df,                          "全馬"),
        evaluate(df[unpop],                   "4番人気以下"),
        evaluate(df[df["win_popularity"]>=6], "6番人気以下"),
        evaluate(df[ana_abc],                 "穴ぐさA/B/C 全体"),
        evaluate(df[ana_ab],                  "穴ぐさA/B"),
        evaluate(df[ana_a],                   "穴ぐさA"),
    ]
    print_table(base, "【ベースライン】")

    # =========================================================
    # 2. 穴ぐさ × v26指数順位
    # =========================================================
    rows = []
    for rank_thr in [1, 2, 3, 4, 5]:
        in_top = df["base_rank"] <= rank_thr
        rows.append(evaluate(df[in_top],             f"指数{rank_thr}位以内 (全馬)"))
        rows.append(evaluate(df[ana_abc & in_top],   f"穴ぐさA/B/C × 指数{rank_thr}位以内"))
        rows.append(evaluate(df[ana_ab  & in_top],   f"穴ぐさA/B × 指数{rank_thr}位以内"))
        rows.append(evaluate(df[ana_a   & in_top],   f"穴ぐさA × 指数{rank_thr}位以内"))
        rows.append({"label": "---", "n": 0, "win_rate": "", "place_rate": "",
                     "win_roi": "", "place_roi": "", "avg_pop": "", "avg_odds": ""})
    print_table(rows, "【穴ぐさ × v26指数順位 クロス】")

    # =========================================================
    # 3. 穴ぐさ × 指数順位 × 人気絞り
    # =========================================================
    pop_rows = []
    for rank_thr in [1, 2, 3]:
        in_top = df["base_rank"] <= rank_thr
        pop_rows.append(evaluate(df[ana_abc & in_top & unpop],
                                 f"穴ぐさA/B/C × 指数{rank_thr}位以内 × 4番人気以下"))
        pop_rows.append(evaluate(df[ana_ab  & in_top & unpop],
                                 f"穴ぐさA/B × 指数{rank_thr}位以内 × 4番人気以下"))
        pop_rows.append(evaluate(df[ana_a   & in_top & unpop],
                                 f"穴ぐさA × 指数{rank_thr}位以内 × 4番人気以下"))
        pop6 = df["win_popularity"] >= 6
        pop_rows.append(evaluate(df[ana_ab  & in_top & pop6],
                                 f"穴ぐさA/B × 指数{rank_thr}位以内 × 6番人気以下"))
        pop_rows.append({"label": "---", "n": 0, "win_rate": "", "place_rate": "",
                         "win_roi": "", "place_roi": "", "avg_pop": "", "avg_odds": ""})
    print_table(pop_rows, "【穴ぐさ × 指数順位 × 人気フィルタ】")

    # =========================================================
    # 4. 穴ぐさ × 指数順位 × DM-time
    # =========================================================
    dm_df = df[df["time_rank"].notna()].copy()
    dm_rows = []
    for rank_thr in [1, 2, 3]:
        in_top = dm_df["base_rank"] <= rank_thr
        t_top2 = dm_df["time_rank"] <= 2
        b_top2 = dm_df["battle_rank"] <= 2
        dm_rows.append(evaluate(dm_df[ana_ab & in_top & t_top2],
                                f"穴ぐさA/B × 指数{rank_thr}位以内 × DM-time≤2位"))
        dm_rows.append(evaluate(dm_df[ana_ab & in_top & t_top2 & b_top2],
                                f"穴ぐさA/B × 指数{rank_thr}位以内 × DM-time≤2 ∧ battle≤2"))
        dm_rows.append(evaluate(dm_df[ana_a  & in_top & t_top2],
                                f"穴ぐさA × 指数{rank_thr}位以内 × DM-time≤2位"))
        dm_rows.append({"label": "---", "n": 0, "win_rate": "", "place_rate": "",
                        "win_roi": "", "place_roi": "", "avg_pop": "", "avg_odds": ""})
    print_table(dm_rows, "【穴ぐさ × 指数順位 × DM-time クロス】")

    # =========================================================
    # 5. 芝 vs ダート別
    # =========================================================
    surf_rows = []
    for surf_label, surf_mask in [("芝", df["surface"] == "turf"),
                                   ("ダート", df["surface"] == "dirt")]:
        for rank_thr in [1, 2, 3]:
            in_top = (df["base_rank"] <= rank_thr) & surf_mask
            surf_rows.append(evaluate(df[ana_ab & in_top],
                                      f"穴ぐさA/B × 指数{rank_thr}位以内 ({surf_label})"))
        surf_rows.append({"label": "---", "n": 0, "win_rate": "", "place_rate": "",
                          "win_roi": "", "place_roi": "", "avg_pop": "", "avg_odds": ""})
    print_table(surf_rows, "【芝/ダート 別】")

    # =========================================================
    # サンプル数サマリ
    # =========================================================
    print(f"\n{'='*80}")
    print("  【サンプル数サマリ】")
    print(f"{'='*80}")
    sums = {
        "全馬":              len(df),
        "穴ぐさA":           int(ana_a.sum()),
        "穴ぐさA/B":         int(ana_ab.sum()),
        "穴ぐさA/B/C":       int(ana_abc.sum()),
        "指数1位以内":       int((df["base_rank"] <= 1).sum()),
        "指数2位以内":       int((df["base_rank"] <= 2).sum()),
        "指数3位以内":       int((df["base_rank"] <= 3).sum()),
        "穴ぐさA/B × 指数1位": int((ana_ab & (df["base_rank"] <= 1)).sum()),
        "穴ぐさA/B × 指数2位": int((ana_ab & (df["base_rank"] <= 2)).sum()),
        "穴ぐさA/B × 指数3位": int((ana_ab & (df["base_rank"] <= 3)).sum()),
        "穴ぐさA × 指数1位":   int((ana_a  & (df["base_rank"] <= 1)).sum()),
        "穴ぐさA × 指数2位":   int((ana_a  & (df["base_rank"] <= 2)).sum()),
        "穴ぐさA × 指数3位":   int((ana_a  & (df["base_rank"] <= 3)).sum()),
    }
    for k, v_cnt in sums.items():
        print(f"  {k:<30}: {v_cnt:>6}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="20230628")
    parser.add_argument("--end",   default="20260628")
    args = parser.parse_args()

    print(f"\n期間: {args.start} 〜 {args.end}")
    df = load_data(args.start, args.end)
    if df.empty:
        print("データなし")
        return
    run(df)


if __name__ == "__main__":
    main()
