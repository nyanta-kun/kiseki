"""レースタイプ別指数チューニング分析

芝/ダート × 距離帯 × グレード別に以下を検証する:
  1. 各指数のスピアマン相関（どの指数が有効か）
  2. 現行ウェイトのROI
  3. タイプ別最適ウェイトと改善幅

使い方:
  cd backend
  .venv/bin/python scripts/racetype_analysis.py --start 20250101 --end 20251231
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv
load_dotenv(_root.parent / ".env")

sys.path.insert(0, str(_here.parent))
import backtest as bt

COMPOSITE_VERSION = bt.COMPOSITE_VERSION

INDEX_COLS = [
    "composite_index",
    "speed_index",
    "last3f_index",
    "course_aptitude",
    "position_advantage",
    "jockey_index",
    "pace_index",
    "rotation_index",
    "pedigree_index",
    "training_index",
    "anagusa_index",
    "paddock_index",
    "rebound_index",
]

INDEX_LABELS = {
    "composite_index":    "総合",
    "speed_index":        "スピード",
    "last3f_index":       "後3F",
    "course_aptitude":    "コース適性",
    "position_advantage": "枠順",
    "jockey_index":       "騎手",
    "pace_index":         "展開",
    "rotation_index":     "ローテ",
    "pedigree_index":     "血統",
    "training_index":     "調教",
    "anagusa_index":      "穴ぐさ",
    "paddock_index":      "パドック",
    "rebound_index":      "反動",
}

# 分析対象指数（composite除く）
BASE_COLS = [c for c in INDEX_COLS if c != "composite_index"]


def _classify(df: pd.DataFrame) -> pd.DataFrame:
    """レースカテゴリ列を追加する。"""
    df = df.copy()

    def surface(s: str | None) -> str:
        if isinstance(s, str):
            if s.startswith("芝"):
                return "芝"
            if s.startswith("ダ"):
                return "ダート"
        return "その他"

    def dist_cat(d: float) -> str:
        if pd.isna(d):
            return "不明"
        d = int(d)
        if d <= 1400:
            return "スプリント(～1400)"
        if d <= 1800:
            return "マイル(1401-1800)"
        if d <= 2400:
            return "中距離(1801-2400)"
        return "長距離(2401+)"

    def grade_cat(g: str | None) -> str:
        if not isinstance(g, str):
            return "一般"
        g = g.upper()
        if g in ("G1", "GI"):
            return "G1"
        if g in ("G2", "GII"):
            return "G2"
        if g in ("G3", "GIII"):
            return "G3"
        if "OP" in g or "L" in g:
            return "OP/L"
        return "一般"

    df["surface_cat"] = df["surface"].apply(surface)
    df["dist_cat"] = df["distance"].apply(dist_cat)
    df["grade_cat"] = df["grade"].apply(grade_cat)
    df["segment"] = df["surface_cat"] + "×" + df["dist_cat"]
    return df


def _spearman_by_segment(df: pd.DataFrame) -> pd.DataFrame:
    """セグメント × 指数 のスピアマン相関を算出する。"""
    rows = []
    for seg, gdf in df.groupby("segment"):
        n_races = gdf["race_id"].nunique()
        if n_races < 30:
            continue

        row = {"セグメント": seg, "レース数": n_races}
        for col in BASE_COLS:
            sub = gdf[gdf[col].notna() & gdf["finish_position"].notna()]
            if len(sub) < 50:
                row[INDEX_LABELS[col]] = float("nan")
                continue
            # レースごとのρを計算して中央値
            rhos = []
            for _, rg in sub.groupby("race_id"):
                if len(rg) < 3:
                    continue
                x = rg[col].to_numpy(dtype=float)
                y = rg["finish_position"].to_numpy(dtype=float)
                if np.any(np.isnan(x)):
                    continue
                rho, _ = spearmanr(x, y)
                if not np.isnan(rho):
                    rhos.append(rho)
            row[INDEX_LABELS[col]] = round(float(np.median(rhos)), 3) if rhos else float("nan")

        rows.append(row)

    df_out = pd.DataFrame(rows).set_index("セグメント")
    # 列を総合相関の高い順に並べ替え
    idx_cols = [INDEX_LABELS[c] for c in BASE_COLS if INDEX_LABELS[c] in df_out.columns]
    col_means = df_out[idx_cols].mean()
    sorted_cols = col_means.sort_values().index.tolist()  # 負の方が良い（降順位と相関）
    return df_out[["レース数"] + sorted_cols]


def _roi_by_segment(df: pd.DataFrame) -> pd.DataFrame:
    """セグメント別ROI（指数1位馬単勝）を算出する。"""
    rows = []
    for seg, gdf in df.groupby("segment"):
        n_races = gdf["race_id"].nunique()
        if n_races < 30:
            continue
        top1 = gdf.loc[gdf.groupby("race_id")["composite_index"].idxmax()]
        valid = top1[top1["win_odds"].notna() & (top1["win_odds"] > 0)]
        if len(valid) == 0:
            continue
        wins = (valid["finish_position"] == 1).sum()
        payout = valid.loc[valid["finish_position"] == 1, "win_odds"].sum()
        roi = round(float(payout / len(valid) * 100), 1)
        win_rate = round(float(wins / len(valid) * 100), 1)
        avg_odds = round(float(valid.loc[valid["finish_position"] == 1, "win_odds"].mean()), 2) if wins > 0 else 0.0
        rows.append({
            "セグメント": seg,
            "レース数": n_races,
            "勝率%": win_rate,
            "ROI%": roi,
            "平均配当": avg_odds,
        })
    return pd.DataFrame(rows).sort_values("ROI%", ascending=False)


def _optimal_weights_per_segment(
    df: pd.DataFrame,
    top_n_segments: int = 4,
) -> None:
    """ROI改善余地の大きいセグメントで最適ウェイトを探索し、結果を表示する。"""
    from scipy.optimize import minimize

    roi_df = _roi_by_segment(df)
    # ROIが最も低い（改善余地大）セグメントを対象
    targets = roi_df.nsmallest(top_n_segments, "ROI%")["セグメント"].tolist()

    print(f"\n{'─'*72}")
    print(f"  【改善余地大セグメント ウェイト最適化 (top {top_n_segments})】")

    for seg in targets:
        gdf = df[df["segment"] == seg].copy()
        n_races = gdf["race_id"].nunique()
        available = [c for c in BASE_COLS if gdf[c].notna().sum() > n_races * 0.3]
        if len(available) < 3 or n_races < 50:
            continue

        # ベースラインROI（composite_index 使用）
        top1_base = gdf.loc[gdf.groupby("race_id")["composite_index"].idxmax()]
        valid_base = top1_base[top1_base["win_odds"].notna() & (top1_base["win_odds"] > 0)]
        if len(valid_base) == 0:
            continue
        base_roi = float(valid_base.loc[valid_base["finish_position"] == 1, "win_odds"].sum() / len(valid_base) * 100)

        # 最適化: -ROI を最小化
        def neg_roi(w: np.ndarray) -> float:
            w = np.abs(w)
            w = w / w.sum() if w.sum() > 0 else np.ones(len(w)) / len(w)
            # 各レースで加重スコアを計算して1位馬を取得
            gdf2 = gdf.copy()
            cols = available
            vals = gdf2[cols].apply(pd.to_numeric, errors="coerce").fillna(0).to_numpy(dtype=float)
            gdf2["_score"] = vals @ w
            top1_ = gdf2.loc[gdf2.groupby("race_id")["_score"].idxmax()]
            valid_ = top1_[top1_["win_odds"].notna() & (top1_["win_odds"] > 0)]
            if len(valid_) == 0:
                return 0.0
            payout = valid_.loc[valid_["finish_position"] == 1, "win_odds"].sum()
            return -float(payout / len(valid_) * 100)

        w0 = np.ones(len(available)) / len(available)
        result = minimize(neg_roi, w0, method="Nelder-Mead",
                          options={"maxiter": 3000, "xatol": 0.01, "fatol": 0.5})

        opt_w = np.abs(result.x)
        opt_w = opt_w / opt_w.sum()
        opt_roi = -result.fun

        print(f"\n  [{seg}]  n={n_races}レース")
        print(f"  現行ROI: {base_roi:.1f}%  →  最適化ROI: {opt_roi:.1f}%  (Δ{opt_roi - base_roi:+.1f}%)")
        print(f"  最適ウェイト上位5:")
        ranked = sorted(zip(available, opt_w), key=lambda x: -x[1])
        for col, w in ranked[:5]:
            print(f"    {INDEX_LABELS.get(col, col):12s}: {w:.3f}")


def run(df: pd.DataFrame, label: str, optimize: bool = False) -> None:
    """分析を実行して結果を表示する。"""
    df = _classify(df)

    print(f"\n{'='*72}")
    print(f"  レースタイプ別指数分析  {label}")
    print(f"{'='*72}")

    # ─ 1. ROI by segment ─────────────────────────────────────────
    roi_df = _roi_by_segment(df)
    print(f"\n  【セグメント別 単勝ROI（指数1位馬）】")
    print(f"  {'セグメント':<30} {'レース数':>8} {'勝率%':>7} {'ROI%':>8} {'平均配当':>9}")
    print(f"  {'─'*60}")
    for _, r in roi_df.iterrows():
        bar = "▓" * int(r["ROI%"] / 10) if r["ROI%"] > 0 else ""
        marker = " ✅" if r["ROI%"] >= 100 else (" ⚠️" if r["ROI%"] < 80 else "")
        print(
            f"  {r['セグメント']:<30} {int(r['レース数']):>8,} {r['勝率%']:>7.1f} "
            f"{r['ROI%']:>7.1f}%{marker}"
        )

    # ─ 2. Spearman correlation by segment ──────────────────────
    print(f"\n  【セグメント別 指数スピアマン中央値ρ（負値=高いほど着順上位）】")
    rho_df = _spearman_by_segment(df)

    # ヘッダー
    idx_cols = [c for c in rho_df.columns if c != "レース数"]
    header = f"  {'セグメント':<28} {'レース数':>6}  " + "  ".join(f"{c[:4]:>5}" for c in idx_cols)
    print(header)
    print(f"  {'─'*len(header)}")

    for seg, row in rho_df.iterrows():
        vals = []
        for c in idx_cols:
            v = row[c]
            if pd.isna(v):
                vals.append("    -")
            else:
                # 最も低い（負に大きい）2列を★でマーク
                vals.append(f"{v:>5.2f}")
        print(f"  {seg:<28} {int(row['レース数']):>6}  " + "  ".join(vals))

    # ─ 3. 指数別 セグメント間ばらつき ────────────────────────────
    print(f"\n  【指数別 セグメント間ρばらつき（std大=セグメント依存が強い）】")
    print(f"  {'指数':<14} {'平均ρ':>8} {'std':>8} {'min':>8} {'max':>8}  判定")
    print(f"  {'─'*58}")
    idx_col_labels = [c for c in rho_df.columns if c != "レース数"]
    stats_rows = []
    for col in idx_col_labels:
        vals = rho_df[col].dropna()
        if len(vals) < 2:
            continue
        stats_rows.append({
            "指数": col,
            "平均ρ": vals.mean(),
            "std": vals.std(),
            "min": vals.min(),
            "max": vals.max(),
        })
    stats_df = pd.DataFrame(stats_rows).sort_values("std", ascending=False)
    for _, r in stats_df.iterrows():
        judgment = "★チューニング余地大" if r["std"] > 0.03 and abs(r["平均ρ"]) > 0.05 else ""
        print(
            f"  {r['指数']:<14} {r['平均ρ']:>8.3f} {r['std']:>8.3f} "
            f"{r['min']:>8.3f} {r['max']:>8.3f}  {judgment}"
        )

    # ─ 4. 最適ウェイト探索（任意） ──────────────────────────────
    if optimize:
        _optimal_weights_per_segment(df)

    print(f"\n{'='*72}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="レースタイプ別指数チューニング分析")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--version", type=int, default=COMPOSITE_VERSION)
    parser.add_argument("--optimize", action="store_true",
                        help="改善余地大セグメントでウェイト最適化も実行")
    args = parser.parse_args()

    df = bt.load_data(args.start, args.end, version=args.version)
    if df.empty:
        print("データなし")
        return
    df = bt.filter_valid_races(df)
    if df.empty:
        print("有効レースなし")
        return

    run(df, f"{args.start} ～ {args.end} (v{args.version})", optimize=args.optimize)


if __name__ == "__main__":
    main()
