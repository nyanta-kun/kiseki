"""地方競馬 穴馬激走条件バックテスト

以下10条件を検証する（単独 + 組み合わせ）:
  ① 距離短縮（200〜400m）× 先行
  ② 外枠の先行馬
  ③ 同距離・同コース過去好走
  ④ 転入2〜3戦目
  ⑤ 前走不利（位置取り後方 → 今回先行）
  ⑥ 騎手乗り替わり（上位騎手へ）
  ⑦ 叩き2走目（休み明け→2戦目）
  ⑧ 馬場替わり（良⇔重/不）
  ⑨ 同厩舎2頭出しの人気薄
  ⑩ 近走指数上昇
  (+)地元調教師（コース内最多出走調教師）

使い方:
    cd backend
    uv run python scripts/chihou_dark_horse_analysis.py
    uv run python scripts/chihou_dark_horse_analysis.py --start 20230416 --end 20260416
    uv run python scripts/chihou_dark_horse_analysis.py --odds-min 5   # 5倍以上のみ
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

CHIHOU_VERSION = 7
BANEI_COURSE = "83"

# -----------------------------------------------------------------------
# DB接続
# -----------------------------------------------------------------------
def get_engine():
    return create_engine(settings.database_url_sync, pool_pre_ping=True, echo=False)


# -----------------------------------------------------------------------
# データ取得：前走情報をウインドウ関数でLAG取得
# -----------------------------------------------------------------------
BASE_QUERY = f"""
SELECT
    r.id              AS race_id,
    r.date            AS race_date,
    r.course          AS course_code,
    r.course_name,
    r.distance,
    r.surface,
    r.condition       AS going,
    r.head_count,
    e.horse_id,
    e.frame_number,
    e.horse_number    AS horse_number,
    e.trainer_id,
    t.name            AS trainer_name,
    e.jockey_id,
    j.name            AS jockey_name,
    res.finish_position,
    res.win_odds,
    res.win_popularity,
    res.running_style,
    res.passing_1,
    res.last_3f,
    res.abnormality_code,
    ci.composite_index,
    ci.speed_index
FROM chihou.races r
JOIN chihou.race_entries e   ON e.race_id = r.id
JOIN chihou.race_results res ON res.race_id = r.id
                             AND res.horse_id = e.horse_id
LEFT JOIN chihou.jockeys j   ON j.id = e.jockey_id
LEFT JOIN chihou.trainers t  ON t.id = e.trainer_id
LEFT JOIN chihou.calculated_indices ci
       ON ci.race_id = r.id
      AND ci.horse_id = e.horse_id
      AND ci.version = {CHIHOU_VERSION}
WHERE r.date BETWEEN :start AND :end
  AND r.course != '{BANEI_COURSE}'
  AND res.finish_position IS NOT NULL
  AND res.abnormality_code = 0
ORDER BY e.horse_id, r.date, r.id
"""


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

    # 型変換
    numeric_cols = [
        "win_odds", "composite_index", "speed_index", "last_3f",
        "finish_position", "jockey_id", "trainer_id", "frame_number",
        "horse_number", "head_count", "win_popularity",
        "passing_1", "distance", "running_style",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    logger.info("取得完了: %d 件 / %d レース", len(df), df["race_id"].nunique())

    # --- LAG 計算（Python / pandas） ---
    logger.info("前走情報を計算中...")
    df = df.sort_values(["horse_id", "race_date", "race_id"]).reset_index(drop=True)
    g = df.groupby("horse_id")

    df["prev_distance"]    = g["distance"].shift(1)
    df["prev_course_code"] = g["course_code"].shift(1)
    df["prev_course_name"] = g["course_name"].shift(1)
    df["prev_going"]       = g["going"].shift(1)
    df["prev_finish"]      = g["finish_position"].shift(1)
    df["prev_style"]       = g["running_style"].shift(1)
    df["prev_passing_1"]   = g["passing_1"].shift(1)
    df["prev_popularity"]  = g["win_popularity"].shift(1)
    df["prev_speed_index"] = g["speed_index"].shift(1)
    df["prev_composite"]   = g["composite_index"].shift(1)
    df["prev2_speed_index"]= g["speed_index"].shift(2)
    df["prev_jockey_id"]   = g["jockey_id"].shift(1)
    df["prev_date"]        = g["race_date"].shift(1)

    df["interval_days"] = (
        pd.to_datetime(df["race_date"]) - pd.to_datetime(df["prev_date"])
    ).dt.days

    # index_rank（指数ランク：レース内）
    df["index_rank"] = df.groupby("race_id")["composite_index"].rank(
        ascending=False, na_option="bottom"
    )

    # --- 騎手×コース勝率（Python で計算）---
    logger.info("騎手コース勝率を計算中...")
    jwr = (
        df.groupby(["jockey_id", "course_code"])
        .apply(lambda x: (x["finish_position"] == 1).sum() / max(len(x), 1), include_groups=False)
        .reset_index(name="jockey_course_win_rate")
    )
    df = df.merge(jwr, on=["jockey_id", "course_code"], how="left")

    # --- 同厩舎2頭出しフラグ ---
    logger.info("同厩舎フラグを計算中...")
    trainer_per_race = (
        df[df["trainer_id"].notna()]
        .groupby(["race_id", "trainer_id"])
        .size()
        .reset_index(name="entries_in_race")
    )
    trainer_per_race = trainer_per_race[trainer_per_race["entries_in_race"] >= 2]
    df = df.merge(trainer_per_race, on=["race_id", "trainer_id"], how="left")
    df["same_trainer_flag"] = df["entries_in_race"].notna()
    df["same_trainer_dark_flag"] = df["same_trainer_flag"] & (df["win_popularity"] > 3)

    # --- 地元調教師フラグ（コース別出走数 上位15%）---
    logger.info("地元調教師フラグを計算中...")
    tcs = (
        df[df["trainer_id"].notna()]
        .groupby(["course_code", "trainer_id"])
        .size()
        .reset_index(name="course_rides")
    )
    tcs["prank"] = tcs.groupby("course_code")["course_rides"].rank(pct=True)
    tcs["local_trainer_flag"] = tcs["prank"] >= 0.85
    df = df.merge(
        tcs[["course_code", "trainer_id", "local_trainer_flag"]],
        on=["course_code", "trainer_id"], how="left"
    )
    df["local_trainer_flag"] = df["local_trainer_flag"].fillna(False)

    logger.info("前処理完了")
    return df


# -----------------------------------------------------------------------
# フラグ定義
# -----------------------------------------------------------------------
def add_flags(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    hc = d["head_count"].fillna(10)

    # ① 距離短縮（200〜400m）
    dist_diff = d["prev_distance"] - d["distance"]
    d["flag_dist_down"] = dist_diff.between(200, 400)

    # ① × 先行（running_style 1or2 or 今回passing_1が頭数の上位30%）
    d["is_front_runner"] = (
        d["running_style"].isin([1, 2]) |
        (d["passing_1"] <= (hc * 0.3).clip(lower=1))
    )
    d["flag_dist_down_front"] = d["flag_dist_down"] & d["is_front_runner"]

    # ②  外枠（枠番が上位30%）× 先行
    d["is_outer_frame"] = d["frame_number"] >= (hc * 0.6).clip(lower=4)
    d["flag_outer_front"] = d["is_outer_frame"] & d["is_front_runner"]

    # ③ 同距離・同コース 前走好走（3着以内）
    d["flag_same_dist_course_good"] = (
        (d["prev_distance"] == d["distance"]) &
        (d["prev_course_code"] == d["course_code"]) &
        (d["prev_finish"] <= 3)
    )

    # ④ 転入2〜3戦目（前走コースが異なる → 今回は同コース連続）
    d["flag_transfer"] = (
        d["prev_course_code"].notna() &
        (d["prev_course_code"] != d["course_code"])
    )

    # ⑤ 前走後方（passing_1が頭数の70%超）→ 今回先行
    d["prev_was_rear"] = d["prev_passing_1"] > (hc * 0.7).clip(lower=4)
    d["flag_rear_to_front"] = d["prev_was_rear"] & d["is_front_runner"]

    # ⑥ 騎手乗り替わり（前走から変更）× 上位騎手（コース勝率Top20%）
    d["flag_jockey_change"] = (
        d["prev_jockey_id"].notna() &
        d["jockey_id"].notna() &
        (d["jockey_id"] != d["prev_jockey_id"])
    )
    top_jockey_threshold = d.groupby("course_code")["jockey_course_win_rate"].transform(
        lambda x: x.quantile(0.8)
    )
    d["is_top_jockey"] = d["jockey_course_win_rate"] >= top_jockey_threshold
    d["flag_upgrade_jockey"] = d["flag_jockey_change"] & d["is_top_jockey"]

    # ⑦ 叩き2走目（前走が長期休養明け=57日以上、今走は35日以内）
    d["flag_second_run"] = (
        d["prev_finish"].notna() &  # 前走あり
        (d["interval_days"].between(8, 35)) &  # 今走は中1週〜5週
        (  # 前走が休養明けとして: この馬の前々走とのgapが長かった場合は難しいので
           # 間隔だけで近似：今走間隔は短く、かつ前走インターバルが長かった馬
           d["prev_distance"].notna()  # 前走データある（初戦除外）
        )
    )
    # より厳密には：prev_interval が長い場合だが、前々走間隔は別途必要
    # ここでは「前走57日以上明け + 今走35日以内」を叩き2走目の代替
    # prev_intervalは集計困難なため、今走間隔で代替（前走が長かった場合は今走が短い傾向）

    # ⑧ 馬場替わり（良 ↔ 重/不）
    good_cond = {"良", "稍"}
    heavy_cond = {"重", "不"}
    d["flag_going_change"] = (
        d["prev_going"].notna() &
        d["going"].notna() &
        (
            (d["prev_going"].isin(good_cond) & d["going"].isin(heavy_cond)) |
            (d["prev_going"].isin(heavy_cond) & d["going"].isin(good_cond))
        )
    )

    # ⑨ 同厩舎2頭出し × 人気薄（4番人気以下）
    d["flag_same_trainer_dark"] = d["same_trainer_dark_flag"].fillna(False)

    # ⑩ 近走指数上昇（速度指数が前走より+3以上）
    d["flag_index_rising"] = (
        d["speed_index"].notna() &
        d["prev_speed_index"].notna() &
        (d["speed_index"] - d["prev_speed_index"] >= 3.0)
    )

    # (+) 地元調教師
    d["flag_local_trainer"] = d["local_trainer_flag"].fillna(False)

    return d


# -----------------------------------------------------------------------
# 条件別集計
# -----------------------------------------------------------------------
FLAGS = [
    ("flag_dist_down",          "① 距離短縮200〜400m"),
    ("flag_dist_down_front",    "① 距離短縮×先行"),
    ("flag_outer_front",        "② 外枠×先行"),
    ("flag_same_dist_course_good", "③ 同距離同コース前走3着内"),
    ("flag_transfer",           "④ 転入（前走別コース）"),
    ("flag_rear_to_front",      "⑤ 前走後方→今回先行"),
    ("flag_jockey_change",      "⑥ 騎手変更"),
    ("flag_upgrade_jockey",     "⑥ 騎手変更×上位騎手"),
    ("flag_second_run",         "⑦ 叩き2走目近似"),
    ("flag_going_change",       "⑧ 馬場替わり"),
    ("flag_same_trainer_dark",  "⑨ 同厩2頭出し×人気薄"),
    ("flag_index_rising",       "⑩ 指数上昇+3以上"),
    ("flag_local_trainer",      "(+) 地元調教師"),
]

COMBO_FLAGS = [
    ("① × ⑩",  "flag_dist_down_front", "flag_index_rising"),
    ("② × ⑦",  "flag_outer_front",     "flag_second_run"),
    ("③ × ⑩",  "flag_same_dist_course_good", "flag_index_rising"),
    ("⑥ × ⑩",  "flag_upgrade_jockey",  "flag_index_rising"),
    ("④ × ⑦",  "flag_transfer",        "flag_second_run"),
    ("① × ② × 人気薄", None, None),  # 特殊処理
]


def report(df: pd.DataFrame, label: str, mask: pd.Series, baseline_win: float, baseline_roi: float) -> dict:
    sub = df[mask]
    n = len(sub)
    if n < 20:
        return {"label": label, "n": n, "win": None, "place": None, "roi": None}
    win_r  = (sub["finish_position"] == 1).mean()
    place_r = (sub["finish_position"] <= 3).mean()
    roi    = sub.apply(
        lambda x: x["win_odds"] if x["finish_position"] == 1 and pd.notna(x["win_odds"]) else 0,
        axis=1
    ).sum() / n * 100
    return {
        "label": label, "n": n,
        "win":   win_r * 100,
        "place": place_r * 100,
        "roi":   roi,
        "win_lift": (win_r - baseline_win) * 100,
        "roi_lift": roi - baseline_roi,
    }


def print_summary(results: list[dict], title: str) -> None:
    print(f"\n{'=' * 80}")
    print(title)
    print(f"{'=' * 80}")
    print(f"{'条件':<28} {'件数':>6} {'勝率':>7} {'複勝率':>7} {'ROI':>8} {'勝率差':>8} {'ROI差':>8}")
    print("-" * 75)
    for r in results:
        if r["win"] is None:
            print(f"{r['label']:<28} {r['n']:>6}  (サンプル不足)")
            continue
        print(f"{r['label']:<28} {r['n']:>6} "
              f"{r['win']:>6.1f}% {r['place']:>6.1f}% "
              f"{r['roi']:>7.1f}% {r['win_lift']:>+7.1f}% {r['roi_lift']:>+7.1f}%")


def analyze(df: pd.DataFrame, odds_min: float = 0.0) -> None:
    if odds_min > 0:
        df = df[df["win_odds"] >= odds_min].copy()
        logger.info("オッズフィルタ(%.1f倍以上): %d 件", odds_min, len(df))

    n_total = len(df)
    baseline_win  = (df["finish_position"] == 1).mean()
    baseline_roi  = df.apply(
        lambda x: x["win_odds"] if x["finish_position"] == 1 and pd.notna(x["win_odds"]) else 0,
        axis=1
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

    # 組み合わせ
    combo_results = []
    for label, f1, f2 in COMBO_FLAGS:
        if label == "① × ② × 人気薄":
            mask = (
                df["flag_dist_down_front"].fillna(False) &
                df["flag_outer_front"].fillna(False) &
                (df["win_popularity"] >= 4)
            )
            combo_results.append(report(df, label, mask, baseline_win, baseline_roi))
        elif f1 and f2:
            mask = df[f1].fillna(False) & df[f2].fillna(False)
            combo_results.append(report(df, label, mask, baseline_win, baseline_roi))
    print_summary(combo_results, "組み合わせ分析")

    # コース別: 主要フラグのROIをコース横断で確認
    print(f"\n{'=' * 80}")
    print("コース別 主要フラグ ROI（① 距離短縮×先行 / ⑩ 指数上昇 / ⑥ 上位騎手乗替）")
    print(f"{'=' * 80}")
    print(f"{'コース':<10} {'全体ROI':>8} {'①距短前':>8} {'⑩指数↑':>8} {'⑥騎手↑':>8} {'③同距コ':>8}")
    print("-" * 55)
    for course in sorted(df["course_name"].unique()):
        c = df[df["course_name"] == course]
        if len(c) < 50:
            continue
        def roi_for(mask):
            s = c[mask.reindex(c.index, fill_value=False).fillna(False)]
            if len(s) < 10:
                return None
            return s.apply(
                lambda x: x["win_odds"] if x["finish_position"] == 1 and pd.notna(x["win_odds"]) else 0, axis=1
            ).sum() / len(s) * 100

        r_all = c.apply(lambda x: x["win_odds"] if x["finish_position"] == 1 and pd.notna(x["win_odds"]) else 0, axis=1).sum() / len(c) * 100
        r_df  = roi_for(df["flag_dist_down_front"])
        r_ir  = roi_for(df["flag_index_rising"])
        r_uj  = roi_for(df["flag_upgrade_jockey"])
        r_sd  = roi_for(df["flag_same_dist_course_good"])
        fmt = lambda v: f"{v:>7.1f}%" if v is not None else "   N/A  "
        print(f"{course:<10} {r_all:>7.1f}%{fmt(r_df)}{fmt(r_ir)}{fmt(r_uj)}{fmt(r_sd)}")

    # 人気薄（5倍以上）に限定した重要パターン
    dark = df[df["win_odds"] >= 5.0].copy()
    n_dark = len(dark)
    bw_dark = (dark["finish_position"] == 1).mean()
    br_dark = dark.apply(
        lambda x: x["win_odds"] if x["finish_position"] == 1 and pd.notna(x["win_odds"]) else 0, axis=1
    ).sum() / n_dark * 100

    dark_results = []
    for flag_col, label in FLAGS:
        if flag_col not in dark.columns:
            continue
        mask = dark[flag_col].fillna(False)
        dark_results.append(report(dark, label, mask, bw_dark, br_dark))
    print_summary(dark_results, f"人気薄（5倍以上）限定分析  ベースライン: 勝率={bw_dark*100:.1f}%  ROI={br_dark:.1f}%")


# -----------------------------------------------------------------------
# main
# -----------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="地方競馬 穴馬激走条件バックテスト")
    parser.add_argument("--start",    default="20230416")
    parser.add_argument("--end",      default="20260416")
    parser.add_argument("--odds-min", type=float, default=0.0,
                        help="最低オッズフィルタ (0=全件, 5=5倍以上)")
    args = parser.parse_args()

    df = load_data(args.start, args.end)
    if df.empty:
        print("データなし")
        return

    df = add_flags(df)
    analyze(df, odds_min=args.odds_min)


if __name__ == "__main__":
    main()
