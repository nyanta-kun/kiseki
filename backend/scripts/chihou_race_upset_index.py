"""地方競馬 レース波乱度指数の検証（2026-07-23、ユーザー提案）。

発想: 全レース購入・的中率一律向上・全体ROI100%超えは構造的に困難（Phase0〜2で実証済み）。
そこで「馬」を選ぶ前に「レース」を波乱度で選別する。上位人気3頭(単勝10倍以下)で決着する
レースは配当が薄く、高い的中率がないと回収困難。逆に対戦馬の能力が拮抗している
（＝モデル・オッズの上位馬同士の格差が小さい）レースほど、不人気馬が入着する余地が
大きく、複勝・その他券種で妙味が出る可能性がある。

本スクリプトは chihou_rebuild_walkforward.py --dump-csv の honest 予測結果を使い、
レース単位の「波乱度」候補指標を作り、実際の波乱発生率・馬券回収率との関係を検証する。

候補指標（いずれもレース内・発走前に計算可能）:
  gap12       : モデルスコア(is_top3)の1位-2位差（小さいほど拮抗＝波乱度高）
  score_std   : モデルスコアのフィールド内標準偏差（小さいほど拮抗）
  odds_gap12  : 単勝オッズの1番人気-2番人気差（小さいほど拮抗）
  fav_odds    : 1番人気単勝オッズ（低いほど「堅い」レース）

頭数ルール: 複勝は7頭以下だと2着までしか払い戻されないため、複勝関連の集計は
head_count>=8 に限定する（2026-07-23 発見・本番修正済みの教訓を踏襲）。

使い方:
  cd backend
  .venv/bin/python scripts/chihou_race_upset_index.py --csv /path/to/chihou_wf_full.csv
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

UNPOP_ODDS_MIN = 10.0  # 「不人気馬」の単勝オッズ下限


def _place_stats(sub: pd.DataFrame) -> tuple[int, int, float]:
    valid = sub[sub["place_odds"].notna()]
    n = len(valid)
    if n == 0:
        return 0, 0, 0.0
    mask = valid["finish_position"].between(1, 3, inclusive="both")
    hits = int(mask.sum())
    roi = float(valid.loc[mask, "place_odds"].sum()) / n
    return n, hits, roi


def _win_stats(sub: pd.DataFrame) -> tuple[int, int, float]:
    n = len(sub)
    if n == 0:
        return 0, 0, 0.0
    hits = int((sub["finish_position"] == 1).sum())
    roi = float(sub.loc[sub["finish_position"] == 1, "win_odds"].sum()) / n
    return n, hits, roi


def build_race_features(df: pd.DataFrame) -> pd.DataFrame:
    """レース単位の波乱度候補指標を計算する。出走予定馬全体(取消含む)を母集団にする。"""
    rows = []
    for race_id, g in df.groupby("race_id"):
        g = g.sort_values("composite_wf", ascending=False)
        if len(g) < 2:
            continue
        scores = g["composite_wf"].to_numpy()
        odds = pd.to_numeric(g["win_odds"], errors="coerce").dropna().sort_values().to_numpy()
        gap12 = float(scores[0] - scores[1])
        score_std = float(np.std(scores))
        odds_gap12 = float(odds[1] - odds[0]) if len(odds) >= 2 else np.nan
        fav_odds = float(odds[0]) if len(odds) >= 1 else np.nan
        # 上位2頭のレース内「複勝期待値シェア」（Herfindahl型集中度）:
        # composite_wf(is_top3スコア)の総和に対する上位2頭の占有率。
        # 高い=上位2頭でほぼ決着（3着枠の奪い合いが薄い＝波乱度低）
        # 低い=フィールド全体に複勝期待値が分散（3着枠が広く開いている＝波乱度高）
        total_score = float(scores.sum())
        top2_share = float((scores[0] + scores[1]) / total_score) if total_score > 0 else np.nan
        rows.append({
            "race_id": race_id,
            "head_count": g["head_count"].iloc[0],
            "course_name": g["course_name"].iloc[0],
            "quarter": g["quarter"].iloc[0],
            "gap12": gap12,
            "top2_share": top2_share,
            "score_std": score_std,
            "odds_gap12": odds_gap12,
            "fav_odds": fav_odds,
        })
    return pd.DataFrame(rows)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    args = p.parse_args()

    df = pd.read_csv(args.csv)
    print(f"読み込み: {len(df):,}行 / {df['race_id'].nunique():,}レース")

    race_feat = build_race_features(df)
    print(f"レース特徴量: {len(race_feat):,}レース")

    # ── 1. gap12 四分位別: 「不人気馬(単勝>=10)が複勝圏に入る」発生率 ──
    print(f"\n{'='*78}\n  1. gap12(モデル1位-2位差) 四分位別: 波乱発生率・馬券回収率\n{'='*78}")
    race_feat["gap12_q"] = pd.qcut(race_feat["gap12"], 4, labels=["Q1(最拮抗)", "Q2", "Q3", "Q4(最決定的)"])

    race_feat["top2_share_q"] = pd.qcut(
        race_feat["top2_share"], 4, labels=["Q1(分散大=波乱度高)", "Q2", "Q3", "Q4(上位2頭占有=波乱度低)"]
    )

    merged = df.merge(
        race_feat[["race_id", "gap12_q", "gap12", "top2_share_q", "top2_share", "head_count"]],
        on="race_id", suffixes=("", "_r"),
    )
    # head_countは元df由来と一致するはずだが明示的にrace_feat側を使う
    merged["head_count"] = merged["head_count_r"]

    for q in ["Q1(最拮抗)", "Q2", "Q3", "Q4(最決定的)"]:
        qsub = merged[merged["gap12_q"] == q]
        n_races = qsub["race_id"].nunique()
        # 波乱発生率: レース内に単勝>=10で複勝圏(1-3着)に入った馬がいるか(head_count>=8のみ)
        qsub8 = qsub[qsub["head_count"] >= 8]
        upset_flag = qsub8[
            (qsub8["win_odds"] >= UNPOP_ODDS_MIN) & qsub8["finish_position"].between(1, 3)
        ].groupby("race_id").size()
        n_races8 = qsub8["race_id"].nunique()
        upset_rate = len(upset_flag) / n_races8 * 100 if n_races8 else 0.0

        # モデル1位馬(idx_rank_wf==1)の単勝的中率・ROI（全頭数）
        top1 = qsub[qsub["idx_rank_wf"] == 1]
        n1, hits1, roi1 = _win_stats(top1)

        print(f"  {q:<10} n_races={n_races:>6,}  波乱発生率(頭数8+,単勝10倍以上が複勝圏)={upset_rate:5.1f}%  "
              f"指数1位単勝的中率={hits1/n1*100 if n1 else 0:5.1f}% 単勝ROI={roi1:.3f}")

    # ── 2. gap12四分位 × 「不人気馬(単勝>=10 かつ idx_rank<=3)」の複勝成績（head_count>=8）──
    print(f"\n{'='*78}\n  2. gap12四分位別: 不人気馬(単勝>=10∧指数3位以内)の複勝回収率(頭数8+限定)\n{'='*78}")
    pool = merged[(merged["head_count"] >= 8) & (merged["win_odds"] >= UNPOP_ODDS_MIN) & (merged["idx_rank_wf"] <= 3)]
    for q in ["Q1(最拮抗)", "Q2", "Q3", "Q4(最決定的)"]:
        sub = pool[pool["gap12_q"] == q]
        n, hits, roi = _place_stats(sub)
        print(f"  {q:<10} n={n:>5,}  hits={hits:>4}  hit_rate={hits/n*100 if n else 0:5.1f}%  複勝ROI={roi:.3f}")

    # ── 3. 比較対象: fav_odds(1番人気オッズ)別の同様の分析 ──
    print(f"\n{'='*78}\n  3. 1番人気オッズ帯別: 不人気馬(単勝>=10∧指数3位以内)の複勝回収率(頭数8+限定)\n{'='*78}")
    merged["fav_odds_band"] = pd.cut(
        merged["fav_odds"], bins=[0, 1.5, 2.5, 4.0, 7.0, 1000],
        labels=["<1.5(超堅)", "1.5-2.5", "2.5-4.0", "4.0-7.0", "7.0+(混戦)"],
    )
    pool2 = merged[(merged["head_count"] >= 8) & (merged["win_odds"] >= UNPOP_ODDS_MIN) & (merged["idx_rank_wf"] <= 3)]
    for band in ["<1.5(超堅)", "1.5-2.5", "2.5-4.0", "4.0-7.0", "7.0+(混戦)"]:
        sub = pool2[pool2["fav_odds_band"] == band]
        n, hits, roi = _place_stats(sub)
        print(f"  {band:<12} n={n:>5,}  hits={hits:>4}  hit_rate={hits/n*100 if n else 0:5.1f}%  複勝ROI={roi:.3f}")

    # ── 4. top2_share(上位2頭の複勝期待値占有率) 四分位別: 波乱発生率・馬券回収率 ──
    print(f"\n{'='*78}\n  4. top2_share(上位2頭の複勝期待値シェア) 四分位別: 波乱発生率\n{'='*78}")
    for q in ["Q1(分散大=波乱度高)", "Q2", "Q3", "Q4(上位2頭占有=波乱度低)"]:
        qsub = merged[merged["top2_share_q"] == q]
        n_races = qsub["race_id"].nunique()
        qsub8 = qsub[qsub["head_count"] >= 8]
        upset_flag = qsub8[
            (qsub8["win_odds"] >= UNPOP_ODDS_MIN) & qsub8["finish_position"].between(1, 3)
        ].groupby("race_id").size()
        n_races8 = qsub8["race_id"].nunique()
        upset_rate = len(upset_flag) / n_races8 * 100 if n_races8 else 0.0
        top1 = qsub[qsub["idx_rank_wf"] == 1]
        n1, hits1, roi1 = _win_stats(top1)
        avg_share = qsub.drop_duplicates("race_id")["top2_share"].mean()
        print(f"  {q:<18} n_races={n_races:>6,} 平均share={avg_share:.3f}  波乱発生率(頭数8+)={upset_rate:5.1f}%  "
              f"指数1位単勝的中率={hits1/n1*100 if n1 else 0:5.1f}% 単勝ROI={roi1:.3f}")

    # ── 5. top2_share四分位 × 不人気馬(単勝>=10∧指数3位以内)の複勝成績（head_count>=8）──
    print(f"\n{'='*78}\n  5. top2_share四分位別: 不人気馬(単勝>=10∧指数3位以内)の複勝回収率(頭数8+限定)\n{'='*78}")
    pool3 = merged[(merged["head_count"] >= 8) & (merged["win_odds"] >= UNPOP_ODDS_MIN) & (merged["idx_rank_wf"] <= 3)]
    for q in ["Q1(分散大=波乱度高)", "Q2", "Q3", "Q4(上位2頭占有=波乱度低)"]:
        sub = pool3[pool3["top2_share_q"] == q]
        n, hits, roi = _place_stats(sub)
        print(f"  {q:<18} n={n:>5,}  hits={hits:>4}  hit_rate={hits/n*100 if n else 0:5.1f}%  複勝ROI={roi:.3f}")

    print(f"\n{'='*78}\n注意: 探索的検証。有望に見えるセグメントも多重比較の可能性を考慮し、"
          f"chihou_protocol.TEST_START(2026-07-01〜)以降で一度きり評価するまで採用しないこと。\n{'='*78}")


if __name__ == "__main__":
    main()
