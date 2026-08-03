"""地方競馬: 頭数×1番人気オッズの組み合わせROI検証 ＋ 脚質構成(front_density)の検証。

前段の探索（gap12/top2_share/大穴決着の事前特徴）から、頭数・1番人気オッズが
大穴決着との相関が最も一貫していた。本スクリプトはこの2軸を直接組み合わせて
不人気馬(単勝>=10∧指数3位以内)の複勝ROIを検証する（頭数8+限定）。

続けて「脚質構成」(front_density=レース内の先行型割合)を検証する。先行馬が多い
レース(前傾ペース想定)ほど差し・追込馬に展開利が生まれやすいという仮説を、
front_density(レース単位) × 対象馬自身のc_early_n(先行度)の組み合わせで見る。

chihou_rebuild_walkforward.py --dump-csv の honest 予測結果を使用。

使い方:
  cd backend
  .venv/bin/python scripts/chihou_headcount_favodds_pace.py --csv /path/to/chihou_wf_full.csv
"""
from __future__ import annotations

import argparse

import pandas as pd

UNPOP_ODDS_MIN = 10.0


def _place_stats(sub: pd.DataFrame) -> tuple[int, int, float]:
    valid = sub[sub["place_odds"].notna()]
    n = len(valid)
    if n == 0:
        return 0, 0, 0.0
    mask = valid["finish_position"].between(1, 3, inclusive="both")
    hits = int(mask.sum())
    roi = float(valid.loc[mask, "place_odds"].sum()) / n
    return n, hits, roi


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    p.add_argument("--min-n", type=int, default=30)
    args = p.parse_args()

    df = pd.read_csv(args.csv)
    print(f"読み込み: {len(df):,}行 / {df['race_id'].nunique():,}レース")

    df["fav_odds_band"] = pd.cut(
        df["fav_odds"], bins=[0, 2.0, 3.0, 4.5, 1000],
        labels=["<2.0(超堅)", "2.0-3.0", "3.0-4.5", "4.5+(混戦)"],
    )
    df["hc_band"] = pd.cut(
        df["head_count"], bins=[7, 9, 11, 100],
        labels=["8-9", "10-11", "12+"],
    )

    pool = df[(df["head_count"] >= 8) & (df["win_odds"] >= UNPOP_ODDS_MIN) & (df["idx_rank_wf"] <= 3)]

    print(f"\n{'='*86}\n  1. 頭数帯 × 1番人気オッズ帯: 不人気馬(単勝>=10∧指数3位以内)の複勝ROI\n{'='*86}")
    print(f"  {'頭数帯':<10}{'1番人気オッズ帯':<16}{'n':>7}{'hit_rate':>10}{'複勝ROI':>10}")
    for hc in ["8-9", "10-11", "12+"]:
        for fb in ["<2.0(超堅)", "2.0-3.0", "3.0-4.5", "4.5+(混戦)"]:
            sub = pool[(pool["hc_band"] == hc) & (pool["fav_odds_band"] == fb)]
            n, hits, roi = _place_stats(sub)
            if n < args.min_n:
                continue
            print(f"  {hc:<10}{fb:<16}{n:>7,}{hits/n*100 if n else 0:>9.1f}%{roi:>10.3f}")

    # ── 2. front_density(レース内先行馬密度) 四分位別: 不人気馬の複勝ROI ──
    print(f"\n{'='*86}\n  2. front_density(レース内先行馬密度) 四分位別: 不人気馬の複勝ROI(頭数8+)\n{'='*86}")
    race_fd = df.drop_duplicates("race_id")[["race_id", "front_density"]].copy()
    race_fd["fd_q"] = pd.qcut(race_fd["front_density"], 4, labels=["Q1(先行少)", "Q2", "Q3", "Q4(先行過多=前傾ペース)"])
    pool2 = pool.merge(race_fd[["race_id", "fd_q"]], on="race_id")
    for q in ["Q1(先行少)", "Q2", "Q3", "Q4(先行過多=前傾ペース)"]:
        sub = pool2[pool2["fd_q"] == q]
        n, hits, roi = _place_stats(sub)
        print(f"  {q:<24} n={n:>5,}  hit_rate={hits/n*100 if n else 0:5.1f}%  複勝ROI={roi:.3f}")

    # ── 3. 前傾ペース(front_density上位) × 対象馬自身が差し・追込(c_early_n低い) の複勝ROI ──
    print(f"\n{'='*86}\n  3. 前傾ペース想定レース(front_density Q4) での脚質別 不人気馬 複勝ROI(頭数8+)\n{'='*86}")
    hot_pace = pool2[pool2["fd_q"] == "Q4(先行過多=前傾ペース)"].copy()
    hot_pace["style"] = pd.cut(
        hot_pace["c_early_n"], bins=[-0.01, 0.3, 0.6, 1.01],
        labels=["差し・追込(early<=0.3)", "中位(0.3-0.6)", "先行(early>0.6)"],
    )
    for style in ["差し・追込(early<=0.3)", "中位(0.3-0.6)", "先行(early>0.6)"]:
        sub = hot_pace[hot_pace["style"] == style]
        n, hits, roi = _place_stats(sub)
        print(f"  {style:<26} n={n:>5,}  hit_rate={hits/n*100 if n else 0:5.1f}%  複勝ROI={roi:.3f}")

    # 比較: 平常ペース(Q1-Q3)での同じ脚質別ROI
    print(f"\n  --- 比較: 平常ペース(front_density Q1-Q3)での同じ脚質別 複勝ROI ---")
    normal_pace = pool2[pool2["fd_q"] != "Q4(先行過多=前傾ペース)"].copy()
    normal_pace["style"] = pd.cut(
        normal_pace["c_early_n"], bins=[-0.01, 0.3, 0.6, 1.01],
        labels=["差し・追込(early<=0.3)", "中位(0.3-0.6)", "先行(early>0.6)"],
    )
    for style in ["差し・追込(early<=0.3)", "中位(0.3-0.6)", "先行(early>0.6)"]:
        sub = normal_pace[normal_pace["style"] == style]
        n, hits, roi = _place_stats(sub)
        print(f"  {style:<26} n={n:>5,}  hit_rate={hits/n*100 if n else 0:5.1f}%  複勝ROI={roi:.3f}")

    print(f"\n{'='*86}\n注意: 探索的検証。多重比較の可能性を考慮し、chihou_protocol.TEST_START(2026-07-01〜)"
          f"以降で一度きり評価するまで採用しないこと。\n{'='*86}")


if __name__ == "__main__":
    main()
