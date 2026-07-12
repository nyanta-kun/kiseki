"""夏競馬 × 軽量馬 × 馬体重変化 バックテスト

検証仮説:
  - 夏競馬（6-9月、非東京・非中山・非京都・非阪神）の軽量馬
    （牝450kg以下 / 牡セン470kg以下）が市場に過小評価されている
  - 特に前走比で体重が減少（-1〜-9kg）している馬が有望
  - v26指数との組み合わせで更に絞れるか検証

【8. 本番条件再現】セクションが夏穴バッジの本番条件
（牡セン ∧ ≤470kg ∧ 芝 ∧ 前走比-6〜-4kg ∧ 7番人気以上、
 races.py の夏穴バッジ判定・natsu_ana_discord_notify.py と同一）を
そのまま再現する。閾値見直し・OOS再検証時はここを基準にすること。

使い方:
  cd backend
  .venv/bin/python scripts/backtest_summer_lightweight.py
  .venv/bin/python scripts/backtest_summer_lightweight.py --start 20220601 --end 20251001
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

# 夏競馬対象場コード（東京05・中山06・京都08・阪神09 を除く JRA）
SUMMER_COURSES = ("'01','02','03','04','07','10'")

QUERY = text(f"""
SELECT
    r.id                AS race_id,
    r.date              AS date,
    SUBSTRING(r.date, 1, 4) AS year,
    r.surface           AS surface,
    r.course            AS course,
    r.course_name       AS course_name,
    r.distance          AS distance,
    r.grade             AS grade,
    r.race_condition_code AS cond_code,
    h.sex               AS sex,
    rr.horse_number,
    rr.horse_weight,
    rr.weight_change,
    rr.win_popularity,
    rr.finish_position,
    rr.abnormality_code,
    rr.win_odds,
    rr.place_odds,
    ci.composite_index
FROM keiba.race_results rr
JOIN keiba.races   r  ON r.id  = rr.race_id
JOIN keiba.horses  h  ON h.id  = rr.horse_id
LEFT JOIN keiba.calculated_indices ci
    ON ci.race_id  = rr.race_id
   AND ci.horse_id = rr.horse_id
   AND ci.version  = (
       SELECT MAX(version) FROM keiba.calculated_indices
   )
WHERE r.date BETWEEN :start_date AND :end_date
  AND r.course IN ({SUMMER_COURSES})
  AND SUBSTRING(r.date, 5, 2) IN ('06', '07', '08', '09')
  AND r.surface IN ('芝', 'ダ')
ORDER BY r.date, r.id, rr.horse_number
""")

def load_data(start: str, end: str) -> pd.DataFrame:
    with Session(engine) as db:
        rows = db.execute(QUERY, {"start_date": start, "end_date": end}).fetchall()
    cols = [
        "race_id", "date", "year", "surface", "course", "course_name",
        "distance", "grade", "cond_code",
        "sex", "horse_number", "horse_weight", "weight_change",
        "win_popularity", "finish_position", "abnormality_code",
        "win_odds", "place_odds", "composite_index",
    ]
    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return df

    for c in ["horse_weight", "weight_change", "win_popularity",
              "finish_position", "abnormality_code", "win_odds",
              "place_odds", "composite_index"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df["abnormality_code"] = df["abnormality_code"].fillna(0)

    # 異常コードあり / 着順なし → レースごと除外
    bad = df[
        (df["abnormality_code"] > 0) | df["finish_position"].isna()
    ]["race_id"].unique()
    df = df[~df["race_id"].isin(bad)].copy()

    # 複勝が成立する頭数（8頭以上）
    hc = df.groupby("race_id")["horse_number"].count()
    df = df[df["race_id"].isin(hc[hc >= 8].index)].copy()

    # weight_change=999（計不明）を除外
    df = df[df["weight_change"] != 999].copy()

    # v26指数のレース内ランク（欠損があってもレース除外はしない）
    df["idx_rank"] = df.groupby("race_id")["composite_index"].rank(
        method="min", ascending=False
    )

    # 軽量フラグ
    df["is_light"] = (
        ((df["sex"] == "牝") & (df["horse_weight"] <= 450)) |
        ((df["sex"].isin(["牡", "セ"])) & (df["horse_weight"] <= 470))
    )

    print(
        f"対象: {df['race_id'].nunique():,} レース / {len(df):,} 馬 "
        f"({df['year'].min()}〜{df['year'].max()})"
    )
    return df


# ---- ROI 計算 ---------------------------------------------------------------

def evaluate(df_sel: pd.DataFrame, label: str) -> dict:
    """win_odds ベースで単勝・複勝 ROI を計算。"""
    v = df_sel[
        df_sel["win_odds"].notna() & (df_sel["win_odds"] > 0)
    ].copy()
    n = len(v)
    if n == 0:
        return {
            "label": label, "n": 0,
            "勝率": "-", "複勝率": "-",
            "単勝ROI": "-", "複勝ROI": "-", "平均人気": "-",
        }

    wins   = int((v["finish_position"] == 1).sum())
    places = int((v["finish_position"] <= 3).sum())
    win_roi = v.loc[v["finish_position"] == 1, "win_odds"].sum() / n

    vp = v[
        (v["place_odds"].notna()) & (v["place_odds"] > 0) &
        (v["finish_position"] <= 3)
    ]
    place_roi_str = f"{vp['place_odds'].sum() / n:.3f}" if len(vp) > 0 else "n/a"

    return {
        "label":   label,
        "n":       n,
        "勝率":    f"{wins/n*100:.1f}%",
        "複勝率":  f"{places/n*100:.1f}%",
        "単勝ROI": f"{win_roi:.3f}",
        "複勝ROI": place_roi_str,
        "平均人気": f"{v['win_popularity'].mean():.1f}",
    }


def print_table(rows: list[dict], title: str) -> None:
    print(f"\n{'='*76}")
    print(f"  {title}")
    print(f"{'='*76}")
    df = pd.DataFrame(rows)
    if df.empty or (df["n"] == 0).all():
        print("  (データなし)")
        return
    df = df[df["n"] > 0]
    print(df.to_string(index=False, col_space=10))


# ---- 年度別ヘルパー ---------------------------------------------------------

def yearly_roi(df_sel: pd.DataFrame, label: str) -> None:
    """条件フィルタ後の年度別単勝ROIを表示。"""
    v = df_sel[df_sel["win_odds"].notna() & (df_sel["win_odds"] > 0)].copy()
    if v.empty:
        return
    rows = []
    for yr, g in v.groupby("year"):
        n = len(g)
        roi = g.loc[g["finish_position"] == 1, "win_odds"].sum() / n
        rows.append({"year": yr, "n": n,
                     "win_pct": f"{(g['finish_position']==1).mean()*100:.1f}%",
                     "win_roi": f"{roi:.3f}"})
    print(f"\n  {label} — 年度別単勝ROI")
    print(pd.DataFrame(rows).to_string(index=False))


# ---- メイン分析 -------------------------------------------------------------

def run(df: pd.DataFrame) -> None:
    # --- 共通マスク ---
    light    = df["is_light"]
    turf     = df["surface"] == "芝"
    dirt     = df["surface"] == "ダ"
    pop_mid  = df["win_popularity"].between(7, 9)     # 7-9番人気
    pop_low  = df["win_popularity"] >= 10             # 10番人気以下
    pop_hot  = df["win_popularity"].between(1, 3)     # 1-3番人気

    chg_sm   = df["weight_change"].between(-3, -1)    # 小減
    chg_md   = df["weight_change"].between(-9, -4)    # 中減
    chg_dec  = df["weight_change"].between(-9, -1)    # 減少全体(-1〜-9)
    chg_big  = df["weight_change"] <= -10             # 大減

    idx1     = df["idx_rank"] == 1                    # v26指数1位
    idx23    = df["idx_rank"] <= 3                    # v26指数3位以内
    idx_low  = df["idx_rank"] >= 4                    # v26指数4位以下

    # ----------------------------------------------------------------
    # 1. ベースライン
    # ----------------------------------------------------------------
    base = [
        evaluate(df,                          "全馬（ベースライン）"),
        evaluate(df[turf],                    "  芝"),
        evaluate(df[dirt],                    "  ダート"),
        evaluate(df[light],                   "軽量馬（牝450/牡470以下）"),
        evaluate(df[light & turf],            "  軽量×芝"),
        evaluate(df[light & dirt],            "  軽量×ダート"),
        evaluate(df[~light],                  "標準馬"),
    ]
    print_table(base, "【1. ベースライン】")

    # ----------------------------------------------------------------
    # 2. 軽量 × 体重変化
    # ----------------------------------------------------------------
    wt_chg = [
        evaluate(df[light],                        "軽量 全体"),
        evaluate(df[light & turf],                 "軽量 × 芝"),
        evaluate(df[light & turf & chg_big],       "軽量 × 芝 × 大減(≤-10)"),
        evaluate(df[light & turf & chg_md],        "軽量 × 芝 × 中減(-4〜-9)  ← 注目"),
        evaluate(df[light & turf & chg_sm],        "軽量 × 芝 × 小減(-1〜-3)"),
        evaluate(df[light & turf & chg_dec],       "軽量 × 芝 × 減少計(-1〜-9)"),
        evaluate(df[light & dirt],                 "軽量 × ダート"),
        evaluate(df[light & dirt & chg_big],       "軽量 × ダ × 大減(≤-10)"),
        evaluate(df[light & dirt & chg_md],        "軽量 × ダ × 中減(-4〜-9)"),
        evaluate(df[light & dirt & chg_sm],        "軽量 × 小減(-1〜-3)"),
        evaluate(df[light & dirt & chg_dec],       "軽量 × ダ × 減少計(-1〜-9)"),
    ]
    print_table(wt_chg, "【2. 軽量 × 体重変化（芝/ダート別）】")

    # ----------------------------------------------------------------
    # 3. 軽量 × 体重変化 × 人気帯
    # ----------------------------------------------------------------
    pop_cross = [
        evaluate(df[light & turf & chg_md & pop_hot], "軽量×芝×中減 × 1-3人気"),
        evaluate(df[light & turf & chg_md & df["win_popularity"].between(4,6)],
                                                       "軽量×芝×中減 × 4-6人気"),
        evaluate(df[light & turf & chg_md & pop_mid], "軽量×芝×中減 × 7-9人気  ← 注目"),
        evaluate(df[light & turf & chg_md & pop_low], "軽量×芝×中減 × 10人気以下 ← 注目"),
        evaluate(df[light & turf & chg_sm & pop_hot], "軽量×芝×小減 × 1-3人気"),
        evaluate(df[light & turf & chg_sm & pop_mid], "軽量×芝×小減 × 7-9人気"),
        evaluate(df[light & turf & chg_sm & pop_low], "軽量×芝×小減 × 10人気以下"),
        evaluate(df[light & dirt & chg_md & pop_mid], "軽量×ダ×中減 × 7-9人気"),
        evaluate(df[light & dirt & chg_md & pop_low], "軽量×ダ×中減 × 10人気以下"),
    ]
    print_table(pop_cross, "【3. 軽量 × 体重変化 × 人気帯】")

    # ----------------------------------------------------------------
    # 4. v26指数との複合スコア（芝×中減×人気薄）
    # ----------------------------------------------------------------
    idx_cross = [
        evaluate(df[light & turf & chg_md & pop_mid],
                 "軽量×芝×中減×7-9人気 単体"),
        evaluate(df[light & turf & chg_md & pop_mid & idx1],
                 "  + v26指数1位"),
        evaluate(df[light & turf & chg_md & pop_mid & idx23],
                 "  + v26指数3位以内"),
        evaluate(df[light & turf & chg_md & pop_mid & idx_low],
                 "  + v26指数4位以下"),
        evaluate(df[light & turf & chg_md & pop_low],
                 "軽量×芝×中減×10人気以下 単体"),
        evaluate(df[light & turf & chg_md & pop_low & idx1],
                 "  + v26指数1位"),
        evaluate(df[light & turf & chg_md & pop_low & idx23],
                 "  + v26指数3位以内"),
        evaluate(df[light & turf & chg_md & pop_low & idx_low],
                 "  + v26指数4位以下"),
        # 人気薄まとめ（7人気以上）× v26
        evaluate(df[light & turf & chg_md & (df["win_popularity"] >= 7)],
                 "軽量×芝×中減×7人気以上 単体"),
        evaluate(df[light & turf & chg_md & (df["win_popularity"] >= 7) & idx23],
                 "  + v26指数3位以内"),
        evaluate(df[light & turf & chg_md & (df["win_popularity"] >= 7) & idx_low],
                 "  + v26指数4位以下"),
    ]
    print_table(idx_cross, "【4. v26指数との複合スコア（芝×中減×人気薄）】")

    # ----------------------------------------------------------------
    # 5. 性別別（牝450以下 vs 牡セン470以下）
    # ----------------------------------------------------------------
    sex_split = df["sex"].isin(["牡", "セ"])

    sex_cross = [
        evaluate(df[light & sex_split & turf & chg_md & pop_mid],
                 "牡セン軽量 × 芝 × 中減 × 7-9人気"),
        evaluate(df[light & sex_split & turf & chg_md & pop_low],
                 "牡セン軽量 × 芝 × 中減 × 10人気以下"),
        evaluate(df[light & sex_split & turf & chg_md & (df["win_popularity"] >= 7)],
                 "牡セン軽量 × 芝 × 中減 × 7人気以上"),
        evaluate(df[light & ~sex_split & turf & chg_md & pop_mid],
                 "牝軽量(≤450) × 芝 × 中減 × 7-9人気"),
        evaluate(df[light & ~sex_split & turf & chg_md & pop_low],
                 "牝軽量(≤450) × 芝 × 中減 × 10人気以下"),
        evaluate(df[light & ~sex_split & turf & chg_sm & pop_low],
                 "牝軽量(≤450) × 芝 × 小減 × 10人気以下"),
    ]
    print_table(sex_cross, "【5. 性別別（牝450/牡470 閾値）】")

    # ----------------------------------------------------------------
    # 6. 牡セン軽量 × 芝 × 中減 × 7人気以上 の年度別安定性
    # ----------------------------------------------------------------
    core_mask = light & sex_split & turf & chg_md & (df["win_popularity"] >= 7)
    print_table([], "")
    yearly_roi(df[core_mask], "牡セン軽量 × 芝 × 中減 × 7人気以上")

    # ----------------------------------------------------------------
    # 8. 本番条件再現（夏穴バッジ・夏穴通知と同一条件）
    #    races.py 夏穴バッジ / natsu_ana_discord_notify.py:
    #      牡セン ∧ 馬体重≤470kg ∧ 芝 ∧ 前走比 -6〜-4kg ∧ 7番人気以上
    #    ※ 牝馬は本番条件に含まれない。体重変化帯も中減(-4〜-9)より狭い
    # ----------------------------------------------------------------
    chg_prod = df["weight_change"].between(-6, -4)  # 本番の黄金ゾーン
    prod_mask = (
        sex_split
        & (df["horse_weight"] <= 470)
        & turf
        & chg_prod
        & (df["win_popularity"] >= 7)
    )
    prod_rows = [
        evaluate(df[prod_mask], "本番条件: 牡セン≤470×芝×-6〜-4kg×7人気以上"),
        evaluate(df[sex_split & (df["horse_weight"] <= 470) & turf & chg_md
                    & (df["win_popularity"] >= 7)],
                 "  参考: 中減(-4〜-9)に広げた場合"),
        evaluate(df[sex_split & (df["horse_weight"] <= 470) & turf
                    & (df["weight_change"].between(-9, -7))
                    & (df["win_popularity"] >= 7)],
                 "  参考: -7〜-9kg のみ（本番除外帯）"),
    ]
    print_table(prod_rows, "【8. 本番条件再現（夏穴バッジ/通知と同一）】")
    yearly_roi(df[prod_mask], "本番条件（-6〜-4kg）")

    # ----------------------------------------------------------------
    # 7. 全体サマリ
    # ----------------------------------------------------------------
    print(f"\n{'='*76}")
    print("  【サンプル数サマリ】")
    print(f"{'='*76}")
    sums = {
        "全馬":                           len(df),
        "  芝":                           int(turf.sum()),
        "  ダート":                        int(dirt.sum()),
        "軽量馬（牝450/牡470以下）":       int(light.sum()),
        "  軽量×芝":                      int((light & turf).sum()),
        "  軽量×芝×中減(-4〜-9)":         int((light & turf & chg_md).sum()),
        "  軽量×芝×中減×7人気以上":       int((light & turf & chg_md & (df["win_popularity"] >= 7)).sum()),
        "  牡セン軽量×芝×中減×7人気以上": int(core_mask.sum()),
        "v26指数データあり":               int(df["composite_index"].notna().sum()),
    }
    for k, v in sums.items():
        print(f"  {k:<36} {v:>6,}")


def main() -> None:
    parser = argparse.ArgumentParser(description="夏競馬軽量馬バックテスト")
    parser.add_argument("--start", default="20220601", help="開始日 YYYYMMDD")
    parser.add_argument("--end",   default="20251001", help="終了日 YYYYMMDD")
    args = parser.parse_args()

    print(f"期間: {args.start} 〜 {args.end}")
    df = load_data(args.start, args.end)
    if df.empty:
        print("データなし")
        return
    run(df)


if __name__ == "__main__":
    main()
