"""中央競馬 穴馬激走条件バックテスト

以下10条件を検証する（単独 + 組み合わせ）:
  ① 距離延長200〜400m × 差し馬（running_style 3or4）
  ② クラス慣れ（昇級2〜3戦目）
  ③ 前走ハイペース × 先行馬（first_3f速い × prev running_style 1or2）
  ④ 前走不利負け（出遅れ / disadvantage_flag）
  ⑤ コース替わり（競馬場変更 × 同一馬場種別）
  ⑥ 芝ダート替わり
  ⑦ 上位騎手への乗り替わり
  ⑧ 馬場替わり（良・稍 ↔ 重・不良）
  ⑨ 調教良化 ← データなし（スキップ）
  ⑩ 血統適性高 × 人気薄（pedigree_index高 × 人気落ち）

使い方:
    cd backend
    uv run python scripts/jra_dark_horse_analysis.py
    uv run python scripts/jra_dark_horse_analysis.py --start 20230415 --end 20260412
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

JRA_VERSION = 17

BASE_QUERY = f"""
SELECT
    r.id              AS race_id,
    r.date            AS race_date,
    r.course          AS course_code,
    r.course_name,
    r.distance,
    r.surface,
    r.condition       AS going,
    r.race_type_code,
    r.head_count,
    r.first_3f,
    e.horse_id,
    e.frame_number,
    e.horse_number    AS horse_number,
    e.jockey_id,
    e.trainer_id,
    e.prev_jockey_code,
    res.finish_position,
    res.win_odds,
    res.win_popularity,
    res.running_style,
    res.passing_1,
    res.abnormality_code,
    res.last_3f,
    res.time_diff,
    ci.composite_index,
    ci.speed_index,
    ci.jockey_index,
    ci.pedigree_index,
    ci.disadvantage_flag,
    ci.distance_change_index
FROM keiba.races r
JOIN keiba.race_entries e   ON e.race_id = r.id
JOIN keiba.race_results res ON res.race_id = r.id
                             AND res.horse_id = e.horse_id
LEFT JOIN keiba.calculated_indices ci
       ON ci.race_id = r.id
      AND ci.horse_id = e.horse_id
      AND ci.version = {JRA_VERSION}
WHERE r.date BETWEEN :start AND :end
  AND r.surface NOT IN ('障')
  AND res.finish_position IS NOT NULL
ORDER BY e.horse_id, r.date, r.id
"""


def get_engine():
    return create_engine(settings.database_url_sync, pool_pre_ping=True, echo=False)


def load_data(start: str, end: str) -> pd.DataFrame:
    engine = get_engine()
    sd = f"{start[:4]}-{start[4:6]}-{start[6:]}"
    ed = f"{end[:4]}-{end[4:6]}-{end[6:]}"
    logger.info("データ取得中: %s 〜 %s ...", start, end)
    with Session(engine) as db:
        result = db.execute(text(BASE_QUERY), {"start": sd, "end": ed})
        rows = result.fetchall()
        cols = list(result.keys())
    df = pd.DataFrame(rows, columns=cols)

    numeric_cols = [
        "win_odds", "composite_index", "speed_index", "jockey_index",
        "pedigree_index", "distance_change_index", "last_3f", "time_diff",
        "finish_position", "jockey_id", "trainer_id", "frame_number",
        "horse_number", "head_count", "win_popularity",
        "passing_1", "distance", "first_3f",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    logger.info("取得完了: %d 件 / %d レース", len(df), df["race_id"].nunique())

    # --- LAG 計算（horse_id × 日付順） ---
    logger.info("前走情報を計算中...")
    df = df.sort_values(["horse_id", "race_date", "race_id"]).reset_index(drop=True)
    g = df.groupby("horse_id")

    df["prev_distance"]      = g["distance"].shift(1)
    df["prev_course_code"]   = g["course_code"].shift(1)
    df["prev_surface"]       = g["surface"].shift(1)
    df["prev_going"]         = g["going"].shift(1)
    df["prev_race_type"]     = g["race_type_code"].shift(1)
    df["prev2_race_type"]    = g["race_type_code"].shift(2)
    df["prev3_race_type"]    = g["race_type_code"].shift(3)
    df["prev_finish"]        = g["finish_position"].shift(1)
    df["prev_style"]         = g["running_style"].shift(1)
    df["prev_passing1"]      = g["passing_1"].shift(1)
    df["prev_first3f"]       = g["first_3f"].shift(1)
    df["prev_abnormality"]   = g["abnormality_code"].shift(1)
    df["prev_disadvantage"]  = g["disadvantage_flag"].shift(1)
    df["prev_popularity"]    = g["win_popularity"].shift(1)
    df["prev_jockey_id"]     = g["jockey_id"].shift(1)
    df["prev_date"]          = g["race_date"].shift(1)
    df["prev_pedigree"]      = g["pedigree_index"].shift(1)

    df["interval_days"] = (
        pd.to_datetime(df["race_date"]) - pd.to_datetime(df["prev_date"])
    ).dt.days

    # index_rank（指数ランク：レース内）
    df["index_rank"] = df.groupby("race_id")["composite_index"].rank(
        ascending=False, na_option="bottom"
    )

    # 騎手×コース勝率（Python で事前計算）
    logger.info("騎手コース勝率を計算中...")
    jwr = (
        df.groupby(["jockey_id", "course_code"])
        .apply(lambda x: (x["finish_position"] == 1).sum() / max(len(x), 1), include_groups=False)
        .reset_index(name="jockey_course_win_rate")
    )
    df = df.merge(jwr, on=["jockey_id", "course_code"], how="left")

    # 前走ペース指標（距離別 first_3f 中央値との比較）
    logger.info("ペース指標を計算中...")
    pace_median = df.groupby("prev_distance")["prev_first3f"].transform("median")
    df["prev_pace_fast"] = df["prev_first3f"] < pace_median  # 前走 first_3f が中央値より速い

    # 正常完走のみを分析対象とする（LAG計算後にフィルタ）
    # ④ 前走不利負け検出のため、LAG計算前は abnormality_code=0 以外も含める
    before = len(df)
    df = df[df["abnormality_code"] == 0].copy()
    logger.info("異常除外: %d → %d 件", before, len(df))

    logger.info("前処理完了")
    return df


# -----------------------------------------------------------------------
# フラグ定義
# -----------------------------------------------------------------------
def add_flags(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    hc = d["head_count"].fillna(10)

    # ① 距離延長200〜400m × 差し・追い込み（running_style 3or4）
    dist_diff = d["distance"] - d["prev_distance"]
    d["is_closer"] = d["running_style"].isin(["3", "4"])
    d["flag_dist_up_closer"] = dist_diff.between(200, 400) & d["is_closer"]

    # ① 距離延長単独（差し限定なし）
    d["flag_dist_up"] = dist_diff.between(200, 400)

    # ② クラス慣れ：昇級2〜3戦目
    # race_type_code を整数として扱う（11〜19程度）
    curr_cls = pd.to_numeric(d["race_type_code"], errors="coerce")
    prev_cls = pd.to_numeric(d["prev_race_type"], errors="coerce")
    prev2_cls = pd.to_numeric(d["prev2_race_type"], errors="coerce")
    prev3_cls = pd.to_numeric(d["prev3_race_type"], errors="coerce")

    # 昇級1戦目: 前走クラスが低い
    d["is_just_moved_up"] = (prev_cls < curr_cls) & prev_cls.notna()
    # 昇級2戦目: 前走が同クラス、前々走が低いクラス
    d["is_class_up_2nd"] = (prev_cls == curr_cls) & (prev2_cls < curr_cls) & prev2_cls.notna()
    # 昇級3戦目: 前走・前々走が同クラス、3走前が低い
    d["is_class_up_3rd"] = (
        (prev_cls == curr_cls) & (prev2_cls == curr_cls) &
        (prev3_cls < curr_cls) & prev3_cls.notna()
    )
    d["flag_class_up_23"] = d["is_class_up_2nd"] | d["is_class_up_3rd"]

    # ③ 前走ハイペース × 先行馬
    d["prev_was_front"] = d["prev_style"].isin(["1", "2"])
    d["flag_prev_highpace_front"] = d["prev_was_front"] & d["prev_pace_fast"].fillna(False)

    # ④ 前走不利負け（出遅れ abnormality=1 or disadvantage_flag=True）
    d["flag_disadvantage_recover"] = (
        (d["prev_abnormality"] == 1) |
        (d["prev_disadvantage"] == True)  # noqa: E712
    )

    # ⑤ コース替わり（競馬場変更 × 同一馬場種別）
    d["flag_course_change"] = (
        d["prev_course_code"].notna() &
        (d["prev_course_code"] != d["course_code"]) &
        (d["prev_surface"] == d["surface"])
    )

    # ⑤' 差し馬×コース替わり（直線長いコースへ）
    long_straight = {"東京", "阪神", "新潟", "京都"}  # 直線が長い競馬場
    d["to_long_straight"] = d["course_name"].isin(long_straight)
    d["flag_course_change_closer"] = (
        d["flag_course_change"] & d["is_closer"] & d["to_long_straight"]
    )

    # ⑥ 芝ダート替わり
    d["flag_surface_change"] = (
        d["prev_surface"].notna() &
        (d["prev_surface"] != d["surface"]) &
        d["surface"].isin(["芝", "ダ"])
    )
    d["flag_dirt_to_turf"] = (
        (d["prev_surface"] == "ダ") & (d["surface"] == "芝")
    )
    d["flag_turf_to_dirt"] = (
        (d["prev_surface"] == "芝") & (d["surface"] == "ダ")
    )

    # ⑦ 上位騎手への乗り替わり（jockey_index ≥ 62 かつ前走から変更）
    d["flag_jockey_change"] = (
        d["prev_jockey_id"].notna() &
        d["jockey_id"].notna() &
        (d["jockey_id"] != d["prev_jockey_id"])
    )
    d["is_top_jockey"] = d["jockey_index"] >= 62.0
    d["flag_upgrade_jockey"] = d["flag_jockey_change"] & d["is_top_jockey"]

    # または prev_jockey_code が設定されていて騎手が変わった場合も検出
    d["flag_jockey_change_v2"] = (
        d["prev_jockey_code"].notna() &
        d["jockey_id"].notna()
    )

    # ⑧ 馬場替わり（良・稍 ↔ 重・不良）
    good_cond = {"良", "稍重"}
    heavy_cond = {"重", "不良"}
    d["flag_going_change"] = (
        d["prev_going"].notna() &
        d["going"].notna() &
        (
            (d["prev_going"].isin(good_cond) & d["going"].isin(heavy_cond)) |
            (d["prev_going"].isin(heavy_cond) & d["going"].isin(good_cond))
        )
    )
    # 良→重（苦手解消で変わり者）
    d["flag_to_heavy"] = (
        d["prev_going"].isin(good_cond) & d["going"].isin(heavy_cond)
    )

    # ⑩ 血統適性高 × 人気薄（pedigree_index ≥ 60 × 4番人気以下）
    d["flag_pedigree_fit_dark"] = (
        (d["pedigree_index"] >= 60.0) &
        (d["win_popularity"] >= 4)
    )

    # (+) 複合: ① × ⑩（距離延長×血統適性）
    d["flag_dist_up_pedigree"] = d["flag_dist_up"] & (d["pedigree_index"] >= 60.0)

    return d


FLAGS = [
    ("flag_dist_up",               "① 距離延長200〜400m（全）"),
    ("flag_dist_up_closer",        "① 距離延長×差し・追い"),
    ("flag_class_up_23",           "② 昇級2〜3戦目"),
    ("flag_prev_highpace_front",   "③ 前走ハイペース×先行"),
    ("flag_disadvantage_recover",  "④ 前走不利負け"),
    ("flag_course_change",         "⑤ コース替わり"),
    ("flag_course_change_closer",  "⑤ コース替わり×差し×長直線"),
    ("flag_surface_change",        "⑥ 芝ダート替わり（全）"),
    ("flag_turf_to_dirt",          "⑥ 芝→ダート"),
    ("flag_dirt_to_turf",          "⑥ ダート→芝"),
    ("flag_jockey_change",         "⑦ 騎手変更（全）"),
    ("flag_upgrade_jockey",        "⑦ 騎手変更×上位騎手"),
    ("flag_going_change",          "⑧ 馬場替わり（全）"),
    ("flag_to_heavy",              "⑧ 良→重"),
    ("flag_pedigree_fit_dark",     "⑩ 血統適性高×人気薄"),
    ("flag_dist_up_pedigree",      "(+) 距離延長×血統適性"),
]

COMBO_FLAGS = [
    ("①×⑦ 距離延長×上位騎手",      "flag_dist_up",            "flag_upgrade_jockey"),
    ("①×④ 距離延長×前走不利",       "flag_dist_up_closer",     "flag_disadvantage_recover"),
    ("③×⑤ ハイペース前×コース替",    "flag_prev_highpace_front", "flag_course_change"),
    ("⑥×⑦ 芝ダ替×上位騎手",        "flag_surface_change",     "flag_upgrade_jockey"),
    ("④×⑦ 前走不利×上位騎手",       "flag_disadvantage_recover", "flag_upgrade_jockey"),
    ("①×⑧ 距離延長×重馬場",         "flag_dist_up",            "flag_to_heavy"),
]


def report(df: pd.DataFrame, label: str, mask: pd.Series,
           baseline_win: float, baseline_roi: float, min_n: int = 30) -> dict:
    sub = df[mask]
    n = len(sub)
    if n < min_n:
        return {"label": label, "n": n, "win": None, "place": None, "roi": None}
    win_r   = (sub["finish_position"] == 1).mean()
    place_r = (sub["finish_position"] <= 3).mean()
    roi     = sub.apply(
        lambda x: x["win_odds"] if x["finish_position"] == 1 and pd.notna(x["win_odds"]) else 0,
        axis=1,
    ).sum() / n * 100
    return {
        "label": label, "n": n,
        "win":      win_r * 100,
        "place":    place_r * 100,
        "roi":      roi,
        "win_lift": (win_r - baseline_win) * 100,
        "roi_lift": roi - baseline_roi,
    }


def print_summary(results: list[dict], title: str) -> None:
    print(f"\n{'=' * 82}")
    print(title)
    print(f"{'=' * 82}")
    print(f"{'条件':<32} {'件数':>6} {'勝率':>7} {'複勝率':>7} {'ROI':>8} {'勝率差':>8} {'ROI差':>8}")
    print("-" * 80)
    for r in results:
        if r["win"] is None:
            print(f"{r['label']:<32} {r['n']:>6}  (サンプル不足)")
            continue
        print(f"{r['label']:<32} {r['n']:>6} "
              f"{r['win']:>6.1f}% {r['place']:>6.1f}% "
              f"{r['roi']:>7.1f}% {r['win_lift']:>+7.1f}% {r['roi_lift']:>+7.1f}%")


def analyze(df: pd.DataFrame, odds_min: float = 0.0) -> None:
    if odds_min > 0:
        df = df[df["win_odds"] >= odds_min].copy()
        logger.info("オッズフィルタ(%.1f倍以上): %d 件", odds_min, len(df))

    n_total = len(df)
    baseline_win = (df["finish_position"] == 1).mean()
    baseline_roi = df.apply(
        lambda x: x["win_odds"] if x["finish_position"] == 1 and pd.notna(x["win_odds"]) else 0,
        axis=1,
    ).sum() / n_total * 100

    print(f"\n対象レコード: {n_total:,}件  ベースライン: 勝率={baseline_win*100:.1f}%  ROI={baseline_roi:.1f}%")

    # 全体フラグ集計
    results = []
    for flag_col, label in FLAGS:
        if flag_col not in df.columns:
            continue
        mask = df[flag_col].fillna(False)
        results.append(report(df, label, mask, baseline_win, baseline_roi))
    print_summary(results, "各条件 単独分析")

    # 組み合わせ分析
    combo_results = []
    for label, f1, f2 in COMBO_FLAGS:
        mask = df[f1].fillna(False) & df[f2].fillna(False)
        combo_results.append(report(df, label, mask, baseline_win, baseline_roi))
    print_summary(combo_results, "組み合わせ分析")

    # コース別: 主要フラグROI
    print(f"\n{'=' * 82}")
    print("コース別 主要フラグ ROI（①距離延長差し / ④前走不利 / ⑥芝ダ替 / ⑦上位騎手）")
    print(f"{'=' * 82}")
    print(f"{'コース':<10} {'全体ROI':>8} {'①距延差':>8} {'④不利':>8} {'⑥芝ダ':>8} {'⑦騎手↑':>8}")
    print("-" * 55)
    for course in sorted(df["course_name"].dropna().unique()):
        c = df[df["course_name"] == course]
        if len(c) < 100:
            continue

        def roi_for(mask: pd.Series) -> float | None:
            s = c[mask.reindex(c.index, fill_value=False).fillna(False)]
            if len(s) < 15:
                return None
            return s.apply(
                lambda x: x["win_odds"] if x["finish_position"] == 1 and pd.notna(x["win_odds"]) else 0,
                axis=1,
            ).sum() / len(s) * 100

        r_all = c.apply(
            lambda x: x["win_odds"] if x["finish_position"] == 1 and pd.notna(x["win_odds"]) else 0,
            axis=1,
        ).sum() / len(c) * 100
        r_du = roi_for(df["flag_dist_up_closer"])
        r_di = roi_for(df["flag_disadvantage_recover"])
        r_sd = roi_for(df["flag_surface_change"])
        r_uj = roi_for(df["flag_upgrade_jockey"])
        fmt = lambda v: f"{v:>7.1f}%" if v is not None else "   N/A  "
        print(f"{course:<10} {r_all:>7.1f}%{fmt(r_du)}{fmt(r_di)}{fmt(r_sd)}{fmt(r_uj)}")

    # 人気薄（5倍以上）分析
    dark = df[df["win_odds"] >= 5.0].copy()
    n_dark = len(dark)
    bw_dark = (dark["finish_position"] == 1).mean()
    br_dark = dark.apply(
        lambda x: x["win_odds"] if x["finish_position"] == 1 and pd.notna(x["win_odds"]) else 0,
        axis=1,
    ).sum() / n_dark * 100

    dark_results = []
    for flag_col, label in FLAGS:
        if flag_col not in dark.columns:
            continue
        mask = dark[flag_col].fillna(False)
        dark_results.append(report(dark, label, mask, bw_dark, br_dark))
    print_summary(
        dark_results,
        f"人気薄（5倍以上）限定分析  ベースライン: 勝率={bw_dark*100:.1f}%  ROI={br_dark:.1f}%",
    )

    # 昇級馬詳細分析
    print(f"\n{'=' * 82}")
    print("② 昇級馬詳細（1戦目 / 2戦目 / 3戦目）")
    print(f"{'=' * 82}")
    for label, flag in [("昇級1戦目", "is_just_moved_up"), ("昇級2戦目", "is_class_up_2nd"), ("昇級3戦目", "is_class_up_3rd")]:
        if flag not in df.columns:
            continue
        mask = df[flag].fillna(False)
        r = report(df, label, mask, baseline_win, baseline_roi, min_n=20)
        if r["win"] is None:
            print(f"  {label}: {r['n']}件（サンプル不足）")
        else:
            print(f"  {label}: {r['n']:,}件  勝率={r['win']:.1f}%  複勝={r['place']:.1f}%  ROI={r['roi']:.1f}%  (ROI差{r['roi_lift']:+.1f}%)")


def main() -> None:
    parser = argparse.ArgumentParser(description="中央競馬 穴馬激走条件バックテスト")
    parser.add_argument("--start",    default="20230415")
    parser.add_argument("--end",      default="20260412")
    parser.add_argument("--odds-min", type=float, default=0.0)
    args = parser.parse_args()

    df = load_data(args.start, args.end)
    if df.empty:
        print("データなし")
        return

    df = add_flags(df)
    analyze(df, odds_min=args.odds_min)


if __name__ == "__main__":
    main()
