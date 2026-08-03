"""地方競馬 walk-forward honest 予測結果のセグメント・スイープ（Phase2: 非効率性の再探索）。

chihou_rebuild_walkforward.py --dump-csv で保存した walk-forward honest 予測結果
（model-vintage look-ahead・生存者バイアスいずれも排除済み）を使い、
「的中率を変えずROIだけ動かす非対称フィルター」（keirinの勝ちパターン）の
手がかりを探索的にスイープする。

⚠️ 多重比較に関する注意:
  本スクリプトは多数のセグメントを機械的に評価するため、一部は偶然 ROI>1 に
  見える（多重比較の必然）。単独のセグメントで「黒字」と断定しないこと。
  ここで見つかった候補は val 期間内の探索に過ぎず、chihou_protocol.TEST_START
  以降の新規データで確認する（一度きり評価）まで採用しないこと。

使い方:
  cd backend
  .venv/bin/python scripts/chihou_walkforward_sweep.py --csv /path/to/chihou_wf_full.csv
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

MIN_N = 40  # この件数未満のセグメントは表示しない(小標本のノイズ排除)
ROI_FLAG = 1.10  # このROI以上を「候補」として強調表示する


def _win_stats(sub: pd.DataFrame) -> tuple[int, int, float]:
    n = len(sub)
    if n == 0:
        return 0, 0, 0.0
    hits = int((sub["finish_position"] == 1).sum())
    roi = float(sub.loc[sub["finish_position"] == 1, "win_odds"].sum()) / n
    return n, hits, roi


def _place_stats(sub: pd.DataFrame) -> tuple[int, int, float]:
    valid = sub[sub["place_odds"].notna()]
    n = len(valid)
    if n == 0:
        return 0, 0, 0.0
    mask = valid["finish_position"].between(1, 3, inclusive="both")
    hits = int(mask.sum())
    roi = float(valid.loc[mask, "place_odds"].sum()) / n
    return n, hits, roi


def sweep(df: pd.DataFrame, group_cols: list[str], bet: str, label: str) -> pd.DataFrame:
    rows = []
    for keys, g in df.groupby(group_cols, dropna=False, observed=True):
        if not isinstance(keys, tuple):
            keys = (keys,)
        if bet == "win":
            n, hits, roi = _win_stats(g)
        else:
            n, hits, roi = _place_stats(g)
        if n < MIN_N:
            continue
        rows.append({**dict(zip(group_cols, keys)), "n": n, "hits": hits,
                     "hit_rate": hits / n * 100 if n else 0.0, "roi": roi})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out = out.sort_values("roi", ascending=False)
    n_tested = len(out)
    n_flagged = int((out["roi"] >= ROI_FLAG).sum())
    print(f"\n{'='*78}\n  [{label}] group={group_cols} bet={bet}  "
          f"(セグメント数={n_tested}, ROI>={ROI_FLAG}候補={n_flagged})\n{'='*78}")
    print(out.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    args = p.parse_args()

    df = pd.read_csv(args.csv)
    print(f"読み込み: {len(df):,}行 / {df['race_id'].nunique():,}レース "
          f"({df['quarter'].nunique()}四半期)")

    # オッズ帯・指数順位帯のビン化
    df["odds_band"] = pd.cut(
        df["win_odds"], bins=[0, 2, 4, 7, 10, 20, 50, 1000],
        labels=["<2", "2-4", "4-7", "7-10", "10-20", "20-50", "50+"],
    )
    df["idx_bucket"] = pd.cut(
        df["idx_rank_wf"], bins=[0, 1, 2, 3, 5, 99],
        labels=["1", "2", "3", "4-5", "6+"],
    )
    df["mkt_agree"] = np.where(df["idx_rank_wf"] == 1,
                                np.where(df["win_popularity"] == 1, "一致(1位=1番人気)", "不一致(1位≠1番人気)"),
                                "指数1位以外")
    # 外部指数(kichiuma/netkeiba)とモデルの一致状況
    df["ext_agree"] = np.where(
        df["ext_missing"] == 1, "外部指数欠損",
        np.where((df["kc_rank_n"] <= 1 / df["head_count"].clip(lower=1)) |
                 (df["nk_rank_n"] <= 1 / df["head_count"].clip(lower=1)),
                 "外部指数も1位評価", "外部指数は非1位評価"),
    )

    # ── 1. 場 × 指数順位帯 (win) ──
    sweep(df, ["course_name", "idx_bucket"], "win", "場×指数順位帯")

    # ── 2. 場 × オッズ帯 (win, 指数1位のみ) ──
    top1 = df[df["idx_rank_wf"] == 1]
    sweep(top1, ["course_name", "odds_band"], "win", "場×オッズ帯(指数1位限定)")

    # ── 3. 市場一致/不一致 × 場 (win, 指数1位のみ) ──
    sweep(top1, ["course_name", "mkt_agree"], "win", "市場一致状況×場(指数1位限定)")

    # ── 4. 距離帯 × サーフェス (win, 指数1位のみ) ──
    top1 = top1.copy()
    top1["dist_band"] = pd.cut(top1["distance"], bins=[0, 1200, 1500, 1800, 2100, 9999],
                                labels=["~1200", "1201-1500", "1501-1800", "1801-2100", "2101+"])
    top1["surface"] = np.where(top1["is_turf"] == 1, "芝", "ダート")
    sweep(top1, ["surface", "dist_band"], "win", "サーフェス×距離帯(指数1位限定)")

    # ── 5. 外部指数一致状況 × オッズ帯 (win, 指数1位限定) ──
    sweep(top1, ["ext_agree", "odds_band"], "win", "外部指数一致状況×オッズ帯(指数1位限定)")

    # ── 6. place_bet相当母集団(断然人気R×指数上位×単勝>=10)を場別に細分 ──
    fav_races = df.groupby("race_id")["win_odds"].transform("min") < 2.0
    place_pop = df[fav_races & (df["idx_rank_wf"] <= 3) & (df["win_odds"] >= 10)]
    sweep(place_pop, ["course_name"], "place", "断然人気R×指数上位×単勝≥10 の場別(place_bet母集団)")

    print(f"\n{'='*78}\n注意: 上記は探索的スイープ。ROI>={ROI_FLAG}のセグメントも多重比較の"
          f"必然として現れうる。有望候補は chihou_protocol.TEST_START(2026-07-01〜)の"
          f"新規データで一度きり評価するまで採用しないこと。\n{'='*78}")


if __name__ == "__main__":
    main()
