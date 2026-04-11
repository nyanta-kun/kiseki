"""買い方のメリハリ戦略 — ROI比較分析

ベースライン（全レース均等100円）と以下の選択的ベット戦略を比較する:

  Strategy 0: ベースライン（全レース均等購入）
  Strategy 1: 指数差フィルタ（gap>=6のみ購入）
  Strategy 2: デッドゾーン回避（gap<3 または gap>=6）
  Strategy 3: 穴馬 × rotation_index フィルタ
  Strategy 4: 複合戦略（gap>=6 or 穴馬×rotation）
  Strategy 5: ベット額メリハリ（gap別に増額）

使い方:
  cd backend
  .venv/bin/python scripts/betting_strategy_analysis.py --start 20260101 --end 20260315
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_here = Path(__file__).resolve()
_root = _here.parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv
load_dotenv(_root.parent / ".env")

# backtest.py の load_data / filter_valid_races を再利用
sys.path.insert(0, str(_here.parent))
import backtest as bt

COMPOSITE_VERSION = bt.COMPOSITE_VERSION


# ---------------------------------------------------------------------------
# 戦略定義
# ---------------------------------------------------------------------------

def _race_features(df: pd.DataFrame) -> pd.DataFrame:
    """レースごとの特徴量を計算して df に追加する。

    追加列:
        gap12: 1位-2位の指数差
        rank_in_race: そのレース内での指数順位（1=最高）
        rotation_rank: rotation_index のレース内順位（1=最高）
        anagusa_rank: anagusa_index のレース内順位（1=最高）
        is_anagusa: オッズ10倍以上かつ指数Top3 (穴馬候補)
    """
    df = df.copy()
    df["rank_in_race"] = df.groupby("race_id")["composite_index"].rank(
        ascending=False, method="min"
    )
    df["rotation_rank"] = df.groupby("race_id")["rotation_index"].rank(
        ascending=False, method="min", na_option="bottom"
    )
    df["anagusa_rank"] = df.groupby("race_id")["anagusa_index"].rank(
        ascending=False, method="min", na_option="bottom"
    )

    # gap12: レース内1位-2位の指数差
    race_top2 = (
        df.groupby("race_id")["composite_index"]
        .nlargest(2)
        .groupby(level=0)
        .agg(list)
    )
    gap12_map = race_top2.apply(
        lambda v: v[0] - v[1] if len(v) >= 2 else 0.0
    ).to_dict()
    df["gap12"] = df["race_id"].map(gap12_map)

    # 穴馬候補: オッズ10倍以上 かつ 指数Top3
    df["is_anagusa"] = (
        df["win_odds"].notna()
        & (df["win_odds"] >= 10.0)
        & (df["rank_in_race"] <= 3)
    )

    return df


def _eval_strategy(
    races: pd.DataFrame,
    selector: pd.Series,  # 購入対象行のbool mask (指数1位馬行のみ想定)
    multiplier: pd.Series | None = None,  # 購入倍率（Noneなら全て1倍）
) -> dict:
    """選択された馬へのベット結果を集計する。

    Args:
        races: 指数1位馬のみのDataFrame
        selector: 購入するかどうかのbool Series (index = races.index)
        multiplier: 購入口数 (Noneなら1口=100円)

    Returns:
        {"bets": int, "wins": int, "roi_pct": float, "win_rate_pct": float,
         "coverage_pct": float, "avg_odds": float, "total_wagered": int}
    """
    target = races[selector].copy()
    if multiplier is not None:
        target["_mult"] = multiplier[selector].fillna(1.0)
    else:
        target["_mult"] = 1.0

    valid = target[target["win_odds"].notna() & (target["win_odds"] > 0)]
    if len(valid) == 0:
        return {"bets": 0, "wins": 0, "roi_pct": 0.0, "win_rate_pct": 0.0,
                "coverage_pct": 0.0, "avg_odds": 0.0, "total_wagered": 0}

    total_wagered = (valid["_mult"] * 100).sum()
    wins_mask = valid["finish_position"] == 1
    payout = (valid.loc[wins_mask, "win_odds"] * valid.loc[wins_mask, "_mult"] * 100).sum()
    n_wins = wins_mask.sum()
    roi = float(payout / total_wagered * 100) if total_wagered > 0 else 0.0
    avg_odds = float(valid.loc[wins_mask, "win_odds"].mean()) if n_wins > 0 else 0.0

    return {
        "bets": len(valid),
        "wins": int(n_wins),
        "roi_pct": round(roi, 1),
        "win_rate_pct": round(float(n_wins / len(valid) * 100), 1),
        "coverage_pct": round(len(valid) / len(races) * 100, 1),
        "avg_odds": round(avg_odds, 2),
        "total_wagered": int(total_wagered),
    }


def _eval_anagusa_strategy(
    df: pd.DataFrame,
    gap_threshold: float | None = None,
    rotation_top_n: int = 5,
    min_odds: float = 10.0,
) -> dict:
    """穴馬ベット戦略の評価（指数Top3 × オッズ条件 × rotation条件）。

    穴馬候補（指数Top3かつオッズ≥min_odds）のうち、
    rotation_indexが上位rotation_top_n以内の馬を購入する。
    gap_thresholdを指定すると、そのレース内でgap12>=thresholdの
    レースのみ対象にする。
    """
    target = df[df["is_anagusa"]].copy()
    if gap_threshold is not None:
        target = target[target["gap12"] < gap_threshold]  # 拮抗レースのみ穴馬狙い
    target = target[target["rotation_rank"] <= rotation_top_n]

    valid = target[target["win_odds"].notna() & (target["win_odds"] > 0)]
    if len(valid) == 0:
        return {"bets": 0, "wins": 0, "roi_pct": 0.0, "win_rate_pct": 0.0,
                "avg_odds": 0.0}

    wins_mask = valid["finish_position"] == 1
    n_wins = wins_mask.sum()
    payout = valid.loc[wins_mask, "win_odds"].sum()
    roi = float(payout / len(valid) * 100)
    avg_odds = float(valid.loc[wins_mask, "win_odds"].mean()) if n_wins > 0 else 0.0

    return {
        "bets": len(valid),
        "wins": int(n_wins),
        "roi_pct": round(roi, 1),
        "win_rate_pct": round(float(n_wins / len(valid) * 100), 1),
        "avg_odds": round(avg_odds, 2),
    }


def run_quarterly(df: pd.DataFrame, label: str) -> None:
    """四半期別 A1戦略(穴馬×rotation上位3位) vs ベースライン比較。"""
    df = _race_features(df)

    df["quarter"] = df["date"].astype(str).str[:6].apply(
        lambda ym: f"{ym[:4]}-Q{(int(ym[4:6])-1)//3+1}"
    )

    print(f"\n{'='*80}")
    print(f"  長期検証: 四半期別ROI  {label}")
    print(f"{'='*80}")
    print(f"\n  {'期間':<12} {'全本命':>18} {'穴馬(無条件)':>18} {'穴馬(rot上位3)':>18} {'穴馬(rot上位5)':>18}")
    print(f"  {'─'*82}")

    totals: dict[str, dict] = {k: {"bets": 0, "payout": 0.0, "wagered": 0} for k in [
        "base", "anagusa_raw", "anagusa_rot3", "anagusa_rot5"]}

    for q, qdf in df.groupby("quarter"):
        top1 = qdf.loc[qdf.groupby("race_id")["composite_index"].idxmax()]
        valid = top1[top1["win_odds"].notna() & (top1["win_odds"] > 0)]

        def _roi(bets_df: pd.DataFrame) -> str:
            if len(bets_df) == 0:
                return "   -R /   -%"
            wins = bets_df["finish_position"] == 1
            payout = bets_df.loc[wins, "win_odds"].sum()
            roi = payout / len(bets_df) * 100
            return f"{len(bets_df):>4}R /{roi:>6.1f}%"

        an_all   = qdf[qdf["is_anagusa"] & qdf["win_odds"].notna()]
        an_rot3  = qdf[qdf["is_anagusa"] & (qdf["rotation_rank"] <= 3) & qdf["win_odds"].notna()]
        an_rot5  = qdf[qdf["is_anagusa"] & (qdf["rotation_rank"] <= 5) & qdf["win_odds"].notna()]

        # 累計集計
        for key, sub in [("base", valid), ("anagusa_raw", an_all), ("anagusa_rot3", an_rot3), ("anagusa_rot5", an_rot5)]:
            totals[key]["bets"] += len(sub)
            totals[key]["payout"] += sub.loc[sub["finish_position"] == 1, "win_odds"].sum()

        print(
            f"  {q:<12} {_roi(valid):>18} {_roi(an_all):>18} "
            f"{_roi(an_rot3):>18} {_roi(an_rot5):>18}"
        )

    # 合計行
    def _total_roi(key: str) -> str:
        b, p = totals[key]["bets"], totals[key]["payout"]
        if b == 0:
            return "   -B /   -%"
        return f"{b:>5}B /{p/b*100:>6.1f}%"

    print(f"  {'─'*82}")
    print(f"  {'【合計】':<12} {_total_roi('base'):>18} {_total_roi('anagusa_raw'):>18} "
          f"{_total_roi('anagusa_rot3'):>18} {_total_roi('anagusa_rot5'):>18}")


def run_analysis(df: pd.DataFrame, label: str) -> None:
    """戦略比較を実行して結果を表示する。"""
    df = _race_features(df)
    top1 = df.loc[df.groupby("race_id")["composite_index"].idxmax()].copy()
    n_races = len(top1)

    print(f"\n{'='*72}")
    print(f"  買い方メリハリ戦略比較  {label}")
    print(f"  対象レース数: {n_races:,}")
    print(f"{'='*72}")

    # ── 基本戦略（指数1位馬への単勝） ─────────────────────────────
    strategies: list[tuple[str, pd.Series, pd.Series | None]] = [
        (
            "S0: ベースライン（全レース均等）",
            pd.Series(True, index=top1.index),
            None,
        ),
        (
            "S1: 指数差≥3のみ購入",
            top1["gap12"] >= 3,
            None,
        ),
        (
            "S2: 指数差≥6のみ購入（高確信）",
            top1["gap12"] >= 6,
            None,
        ),
        (
            "S3: デッドゾーン回避（gap<3 or gap≥6）",
            (top1["gap12"] < 3) | (top1["gap12"] >= 6),
            None,
        ),
        (
            "S4: 指数差≥3 かつ gap<3は除外",
            top1["gap12"] >= 3,
            None,
        ),
    ]

    # ベット額メリハリ（gap別増額）
    mult = pd.Series(1.0, index=top1.index)
    mult[top1["gap12"] >= 6] = 3.0
    mult[top1["gap12"] >= 10] = 5.0
    mult[(top1["gap12"] >= 3) & (top1["gap12"] < 6)] = 0.5  # 死に体ゾーンは半減
    strategies.append(
        (
            "S5: ベット額メリハリ（gap<3:×1, 3-6:×0.5, ≥6:×3, ≥10:×5）",
            pd.Series(True, index=top1.index),
            mult,
        )
    )

    print(f"\n{'─'*72}")
    print(f"  【指数1位馬 単勝戦略比較】")
    print(f"  {'戦略':<46} {'レース':>6} {'的中':>5} {'勝率':>7} {'ROI':>8} {'平均配当':>8} {'カバー率':>8}")
    print(f"  {'─'*70}")

    for name, sel, mul in strategies:
        r = _eval_strategy(top1, sel, mul)
        coverage = r["coverage_pct"] if mul is None else "-"
        print(
            f"  {name:<46} {r['bets']:>6,} {r['wins']:>5} "
            f"{r['win_rate_pct']:>6.1f}% {r['roi_pct']:>7.1f}% "
            f"{r['avg_odds']:>7.2f}倍 "
            f"{str(coverage):>7}%"
        )

    # ── 穴馬戦略（指数Top3 × rotation × odds） ──────────────────
    print(f"\n{'─'*72}")
    print(f"  【穴馬ベット戦略（オッズ≥10倍 × 指数Top3）】")
    print(f"  {'戦略':<52} {'賭数':>5} {'的中':>4} {'勝率':>7} {'ROI':>8} {'平均配当':>8}")
    print(f"  {'─'*70}")

    anagusa_strategies = [
        ("A0: 全穴馬候補（フィルタなし）", None, 99),
        ("A1: rotation上位3位以内", None, 3),
        ("A2: rotation上位5位以内", None, 5),
        ("A3: 拮抗レース(gap<3)のみ + rotation上位5位", 3.0, 5),
        ("A4: 拮抗レース(gap<6)のみ + rotation上位3位", 6.0, 3),
    ]

    for name, gap_th, rot_n in anagusa_strategies:
        r = _eval_anagusa_strategy(df, gap_threshold=gap_th, rotation_top_n=rot_n)
        if r["bets"] == 0:
            print(f"  {name:<52} {'データなし':>40}")
            continue
        print(
            f"  {name:<52} {r['bets']:>5} {r['wins']:>4} "
            f"{r['win_rate_pct']:>6.1f}% {r['roi_pct']:>7.1f}% "
            f"{r['avg_odds']:>7.2f}倍"
        )

    # ── 複合戦略（高確信 + 穴馬フィルタの組み合わせ） ───────────
    print(f"\n{'─'*72}")
    print(f"  【複合戦略（高確信レース + 穴馬セレクト）】")

    # 複合A: gap>=6の通常ベット + 全レースの穴馬(rot上位3位)
    combo_top1_sel = top1["gap12"] >= 6
    r_combo_top1 = _eval_strategy(top1, combo_top1_sel)
    r_anagusa_combo = _eval_anagusa_strategy(df, gap_threshold=None, rotation_top_n=3)

    combo_bets = r_combo_top1["bets"] + r_anagusa_combo["bets"]
    combo_wins_payout = (
        # gap>=6の1位馬勝利分
        top1[combo_top1_sel & top1["win_odds"].notna() & (top1["finish_position"] == 1)]["win_odds"].sum()
    )
    # 穴馬分
    anagusa_target = df[df["is_anagusa"] & (df["rotation_rank"] <= 3) & df["win_odds"].notna()]
    combo_wins_payout += anagusa_target[anagusa_target["finish_position"] == 1]["win_odds"].sum()
    combo_roi = round(combo_wins_payout / combo_bets * 100, 1) if combo_bets > 0 else 0.0
    combo_wins = (
        int(top1[combo_top1_sel & (top1["finish_position"] == 1)].shape[0])
        + int(anagusa_target[anagusa_target["finish_position"] == 1].shape[0])
    )

    print(f"\n  C1: gap≥6の本命 + 全レース穴馬(rotation上位3位)")
    print(f"      本命ベット: {r_combo_top1['bets']}レース ROI {r_combo_top1['roi_pct']}%")
    print(f"      穴馬ベット: {r_anagusa_combo['bets']}頭 ROI {r_anagusa_combo['roi_pct']}%")
    print(f"      合計:       {combo_bets}ベット {combo_wins}的中 ROI {combo_roi}%")

    # ── 月別/日別ROI推移（S2: gap>=6） ─────────────────────────
    print(f"\n{'─'*72}")
    print(f"  【月別ROI（S2: gap≥6のみ購入 vs ベースライン）】")
    top1_valid = top1[top1["win_odds"].notna() & (top1["win_odds"] > 0)].copy()
    top1_valid["ym"] = top1_valid["date"].astype(str).str[:6]

    monthly_rows = []
    for ym, g in top1_valid.groupby("ym"):
        base_wins = (g["finish_position"] == 1).sum()
        base_payout = g.loc[g["finish_position"] == 1, "win_odds"].sum()
        base_roi = round(base_payout / len(g) * 100, 1) if len(g) > 0 else 0.0

        gap6 = g[g["gap12"] >= 6]
        g6_wins = (gap6["finish_position"] == 1).sum()
        g6_payout = gap6.loc[gap6["finish_position"] == 1, "win_odds"].sum()
        g6_roi = round(g6_payout / len(gap6) * 100, 1) if len(gap6) > 0 else 0.0

        monthly_rows.append({
            "月": ym,
            "全レース(本命)": f"{len(g)}R/{base_wins}的中/{base_roi}%",
            "gap≥6": f"{len(gap6)}R/{g6_wins}的中/{g6_roi}%" if len(gap6) > 0 else "-",
        })

    for r in monthly_rows:
        print(f"  {r['月']}: 全={r['全レース(本命)']}  gap≥6={r['gap≥6']}")

    print(f"\n{'='*72}\n")


def main() -> None:
    """エントリーポイント。"""
    parser = argparse.ArgumentParser(description="買い方メリハリ戦略分析")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument(
        "--version", type=int, default=COMPOSITE_VERSION,
        help=f"算出バージョン (default: {COMPOSITE_VERSION})"
    )
    parser.add_argument(
        "--quarterly", action="store_true",
        help="四半期別長期検証モード"
    )
    args = parser.parse_args()

    df = bt.load_data(args.start, args.end, version=args.version)
    if df.empty:
        print("データなし")
        return
    df = bt.filter_valid_races(df)
    if df.empty:
        print("有効レースなし")
        return

    label = f"{args.start} ～ {args.end} (v{args.version})"
    if args.quarterly:
        run_quarterly(df, label)
    else:
        run_analysis(df, label)


if __name__ == "__main__":
    main()
