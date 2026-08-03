"""穴推奨 × DM-time 上位 クロス検証

「穴推奨（穴ぐさA/B、人気薄）」と「JRA-VAN NEXT タイム指数DM上位」が
重なった場合の単勝率・複勝率・ROI を検証する。

使い方:
  .venv/bin/python scripts/analyze_anagusa_dm_cross.py [--start YYYYMMDD] [--end YYYYMMDD]
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

# sekito course_code → keiba 2桁コード
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
  AND ci.version = (
      SELECT MAX(version) FROM keiba.calculated_indices
  )
ORDER BY r.date, r.id, re.horse_number
""")


def load_data(start: str, end: str, min_dm_cov: float = 0.6) -> pd.DataFrame:
    with Session(engine) as db:
        rows = db.execute(QUERY, {"start_date": start, "end_date": end}).fetchall()
    cols = [
        "race_id", "date", "surface", "distance", "horse_id", "horse_number",
        "composite_index", "jvan_time_dm", "jvan_battle_dm",
        "finish_position", "abnormality_code", "win_odds", "place_odds",
        "win_popularity", "anagusa_rank",
    ]
    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return df
    for c in ["composite_index", "jvan_time_dm", "jvan_battle_dm",
              "finish_position", "abnormality_code", "win_odds", "place_odds", "win_popularity"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["abnormality_code"] = df["abnormality_code"].fillna(0)

    # 異常コードあり / 着順なし → レースごと除外
    bad = df[(df["abnormality_code"] > 0) | df["finish_position"].isna()]["race_id"].unique()
    df = df[~df["race_id"].isin(bad)].copy()

    # DM カバレッジフィルタ（カバレッジが低いレースを除外）
    cov = df.groupby("race_id").apply(
        lambda g: g["jvan_time_dm"].notna().mean(), include_groups=False
    )
    keep = cov[cov >= min_dm_cov].index
    df = df[df["race_id"].isin(keep)].copy()

    # 頭数フィルタ（複勝=3着以内が成立する8頭以上）
    hc = df.groupby("race_id")["horse_id"].count()
    df = df[df["race_id"].isin(hc[hc >= 8].index)].copy()

    # ランク付与（レース内 1=最良）
    df["time_rank"]   = df.groupby("race_id")["jvan_time_dm"].rank(method="min", ascending=False)
    df["battle_rank"] = df.groupby("race_id")["jvan_battle_dm"].rank(method="min", ascending=False)
    df["base_rank"]   = df.groupby("race_id")["composite_index"].rank(method="min", ascending=False)

    print(f"対象: {df['race_id'].nunique():,} レース / {len(df):,} 馬 "
          f"(DM coverage ≥ {min_dm_cov:.0%}, 頭数 ≥ 8)")
    return df


def evaluate(df_sel: pd.DataFrame, label: str) -> dict:
    """単勝・複勝の的中率/ROI を計算。place_odds は実払戻が無ければ省略。"""
    v = df_sel[df_sel["win_odds"].notna() & (df_sel["win_odds"] > 0)].copy()
    n = len(v)
    if n == 0:
        return {"label": label, "bets": 0,
                "win_rate": "-", "place_rate": "-",
                "win_roi": "-", "place_roi": "-", "avg_pop": "-"}
    wins   = int((v["finish_position"] == 1).sum())
    places = int((v["finish_position"] <= 3).sum())
    win_roi   = v.loc[v["finish_position"] == 1, "win_odds"].sum() / n
    avg_pop   = v["win_popularity"].mean()

    # 複勝ROI: place_odds は3着以内の馬にしか格納されていないため
    # 分母は全購入対象馬数(n)、分子は3着以内払戻合計
    vp = v[(v["place_odds"].notna()) & (v["place_odds"] > 0) & (v["finish_position"] <= 3)]
    if len(vp) > 0:
        place_roi = vp["place_odds"].sum() / n
        place_roi_str = f"{place_roi:.3f}"
    else:
        place_roi_str = "n/a"

    return {
        "label": label,
        "bets": n,
        "win_rate": f"{wins/n*100:.1f}%",
        "place_rate": f"{places/n*100:.1f}%",
        "win_roi": f"{win_roi:.3f}",
        "place_roi": place_roi_str,
        "avg_pop": f"{avg_pop:.1f}",
    }


def print_table(rows: list[dict], title: str) -> None:
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")
    df = pd.DataFrame(rows)
    if df.empty or df["bets"].eq(0).all():
        print("  (データなし)")
        return
    cols = ["label", "bets", "win_rate", "place_rate", "win_roi", "place_roi", "avg_pop"]
    df = df[[c for c in cols if c in df.columns]]
    print(df.to_string(index=False, col_space=10))


def run(df: pd.DataFrame) -> None:
    ana_ab  = df["anagusa_rank"].isin(["A", "B"])
    ana_a   = df["anagusa_rank"] == "A"
    unpop   = df["win_odds"] >= 10.0          # 単勝10倍以上
    t1      = df["time_rank"]   == 1
    t2      = df["time_rank"]   <= 2
    b1      = df["battle_rank"] == 1
    b2      = df["battle_rank"] <= 2

    # ------------------------------------------------------------------
    # 1. ベースライン
    # ------------------------------------------------------------------
    base = [
        evaluate(df,                        "全馬 (ベースライン)"),
        evaluate(df[unpop],                 "人気薄 (単勝≥10倍)"),
        evaluate(df[ana_ab],                "穴ぐさA/B"),
        evaluate(df[ana_a],                 "穴ぐさA"),
        evaluate(df[df["time_rank"] == 1],  "DM-time 1位"),
        evaluate(df[df["time_rank"] <= 2],  "DM-time ≤2位"),
    ]
    print_table(base, "【ベースライン】")

    # ------------------------------------------------------------------
    # 2. 人気薄 × DM-time
    # ------------------------------------------------------------------
    cross_unpop = [
        evaluate(df[unpop],           "人気薄 単体"),
        evaluate(df[unpop & t1],      "人気薄 × DM-time 1位"),
        evaluate(df[unpop & t2],      "人気薄 × DM-time ≤2位"),
        evaluate(df[unpop & t1 & b2], "人気薄 × DM-time 1位 ∧ battle≤2"),
        evaluate(df[unpop & b1],      "人気薄 × DM-battle 1位"),
        evaluate(df[unpop & t1 & b1], "人気薄 × DM-time 1位 ∧ battle 1位"),
    ]
    print_table(cross_unpop, "【人気薄 (単勝≥10倍) × DM-time クロス】")

    # ------------------------------------------------------------------
    # 3. 穴ぐさA/B × DM-time
    # ------------------------------------------------------------------
    cross_ana = [
        evaluate(df[ana_ab],                  "穴ぐさA/B 単体"),
        evaluate(df[ana_ab & t1],             "穴ぐさA/B × DM-time 1位"),
        evaluate(df[ana_ab & t2],             "穴ぐさA/B × DM-time ≤2位"),
        evaluate(df[ana_ab & t1 & b1],        "穴ぐさA/B × DM-time 1位 ∧ battle 1位"),
        evaluate(df[ana_ab & t2 & b2],        "穴ぐさA/B × DM-time ≤2 ∧ battle ≤2"),
        evaluate(df[ana_a & t1],              "穴ぐさA × DM-time 1位"),
        evaluate(df[ana_a & t2],              "穴ぐさA × DM-time ≤2位"),
    ]
    print_table(cross_ana, "【穴ぐさA/B × DM-time クロス】")

    # ------------------------------------------------------------------
    # 4. 穴ぐさA/B × 人気薄 × DM-time（三重クロス）
    # ------------------------------------------------------------------
    triple = [
        evaluate(df[ana_ab & unpop],          "穴ぐさA/B × 人気薄"),
        evaluate(df[ana_ab & unpop & t1],     "穴ぐさA/B × 人気薄 × DM-time 1位"),
        evaluate(df[ana_ab & unpop & t2],     "穴ぐさA/B × 人気薄 × DM-time ≤2位"),
        evaluate(df[ana_ab & unpop & t1 & b1],"穴ぐさA/B × 人気薄 × DM両方 1位"),
        evaluate(df[ana_a  & unpop & t1],     "穴ぐさA × 人気薄 × DM-time 1位"),
        evaluate(df[ana_a  & unpop & t2],     "穴ぐさA × 人気薄 × DM-time ≤2位"),
    ]
    print_table(triple, "【穴ぐさ × 人気薄 × DM-time 三重クロス】")

    # ------------------------------------------------------------------
    # 5. 既存指数との比較（DM-time だけが高く評価する穴馬）
    # ------------------------------------------------------------------
    dm_only = [
        evaluate(df[unpop & t1 & (df["base_rank"] >= 5)],
                 "人気薄 × DM-time 1位 ∧ 自指数≥5位 (DMだけ評価)"),
        evaluate(df[unpop & t2 & (df["base_rank"] >= 4)],
                 "人気薄 × DM-time ≤2位 ∧ 自指数≥4位"),
        evaluate(df[ana_ab & t1 & (df["base_rank"] >= 5)],
                 "穴ぐさA/B × DM-time 1位 ∧ 自指数≥5位"),
        evaluate(df[ana_ab & unpop & t1 & (df["base_rank"] >= 5)],
                 "穴ぐさA/B × 人気薄 × DM-time 1位 ∧ 自指数≥5位"),
    ]
    print_table(dm_only, "【DM-time のみが高評価する穴馬（自指数との乖離）】")

    # ------------------------------------------------------------------
    # 6. サンプル数サマリ
    # ------------------------------------------------------------------
    print(f"\n{'='*70}")
    print("  【サンプル数サマリ】")
    print(f"{'='*70}")
    sums = {
        "全馬":                len(df),
        "人気薄(単勝≥10)":     int(unpop.sum()),
        "穴ぐさA":              int(ana_a.sum()),
        "穴ぐさA/B":            int(ana_ab.sum()),
        "DM-time 1位":          int(t1.sum()),
        "DM-time ≤2位":         int(t2.sum()),
        "穴ぐさA/B×人気薄":     int((ana_ab & unpop).sum()),
        "穴ぐさA/B×人気薄×t1":  int((ana_ab & unpop & t1).sum()),
        "穴ぐさA×人気薄×t1":    int((ana_a  & unpop & t1).sum()),
    }
    for k, v in sums.items():
        print(f"  {k:<30}: {v:>5}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="20230614")
    parser.add_argument("--end",   default="20260614")
    parser.add_argument("--min-dm-cov", type=float, default=0.6,
                        help="DM取得レースのカバレッジ下限 (0〜1)")
    args = parser.parse_args()

    print(f"\n期間: {args.start} 〜 {args.end}")
    df = load_data(args.start, args.end, args.min_dm_cov)
    if df.empty:
        print("データなし")
        return
    run(df)


if __name__ == "__main__":
    main()
