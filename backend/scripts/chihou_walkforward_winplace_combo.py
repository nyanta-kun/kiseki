"""地方競馬 walk-forward honest 予測を使った「単勝率×複勝率の組み合わせ」検証。

競輪の「単勝率・複勝率を候補とし、この組み合わせから予想を作成する」アプローチを
地方競馬に応用できないか検証する。chihou は既に is_top3ヘッド(composite/place_probability
相当)・is_winヘッド(win_probability)の2ヘッド構成のため、素材はすでに揃っている。

chihou_rebuild_walkforward.py --dump-csv で保存した honest 予測結果を使う
（model-vintage look-ahead・生存者バイアスいずれも排除済み・再学習不要で高速）。

検証観点:
  1. idx_rank_wf(=is_top3モデル順位)==1 と win_rank_wf(=is_winモデル順位)==1 の一致率
  2. 両モデル一致 vs 片方のみ vs 不一致 での単勝ROI比較（一致は「強い軸」シグナルになるか）
  3. 複勝モデルは強い(上位)が単勝モデルは弱い馬=「堅実な2-3着候補」の複勝ROI

使い方:
  cd backend
  .venv/bin/python scripts/chihou_walkforward_winplace_combo.py --csv /path/to/chihou_wf_full.csv
"""
from __future__ import annotations

import argparse

import pandas as pd


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


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    args = p.parse_args()

    df = pd.read_csv(args.csv)
    print(f"読み込み: {len(df):,}行 / {df['race_id'].nunique():,}レース")

    # 複勝モデル(is_top3)順位は既存の idx_rank_wf。単勝モデル(is_win)順位を新規算出。
    df["win_rank_wf"] = (
        df.groupby("race_id")["win_prob_wf"].rank(method="first", ascending=False).astype("Int64")
    )

    # ── 1. 両モデルの1位一致率 ──
    top1 = df[df["idx_rank_wf"] == 1].copy()
    agree = (top1["win_rank_wf"] == 1).mean() * 100
    print(f"\n両モデル1位一致率: {agree:.1f}% (複勝モデル1位={len(top1):,}行中)")

    # ── 2. 一致/不一致での単勝ROI比較 ──
    print(f"\n{'='*70}\n  複勝モデル1位馬の単勝成績: 単勝モデルとの一致状況別\n{'='*70}")
    both = top1[top1["win_rank_wf"] == 1]
    only_place = top1[top1["win_rank_wf"] >= 2]
    n, hits, roi = _win_stats(both)
    print(f"  両モデル一致(win_rank=1)      n={n:,}  hits={hits}  hit_rate={hits/n*100 if n else 0:.1f}%  単勝ROI={roi:.3f}")
    n, hits, roi = _win_stats(only_place)
    print(f"  複勝モデルのみ1位(win_rank>=2) n={n:,}  hits={hits}  hit_rate={hits/n*100 if n else 0:.1f}%  単勝ROI={roi:.3f}")

    # さらに win_rank の値別に細分（2位・3位・4位以上）
    for wr in [2, 3]:
        sub = top1[top1["win_rank_wf"] == wr]
        n, hits, roi = _win_stats(sub)
        print(f"    win_rank_wf=={wr}            n={n:,}  hits={hits}  hit_rate={hits/n*100 if n else 0:.1f}%  単勝ROI={roi:.3f}")
    sub = top1[top1["win_rank_wf"] >= 4]
    n, hits, roi = _win_stats(sub)
    print(f"    win_rank_wf>=4             n={n:,}  hits={hits}  hit_rate={hits/n*100 if n else 0:.1f}%  単勝ROI={roi:.3f}")

    # ── 3. 「堅実な2-3着候補」= 複勝モデル上位(idx_rank<=2) ∧ 単勝モデル弱い(win_rank>=4) の複勝成績 ──
    print(f"\n{'='*70}\n  「堅実プレイス型」候補: 複勝モデル上位2位以内 ∧ 単勝モデルは4位以下\n{'='*70}")
    steady = df[(df["idx_rank_wf"] <= 2) & (df["win_rank_wf"] >= 4)]
    n, hits, roi = _place_stats(steady)
    print(f"  全体      n={n:,}  hits={hits}  hit_rate={hits/n*100 if n else 0:.1f}%  複勝ROI={roi:.3f}")
    # オッズ帯別
    for lo, hi, label in [(1.0, 5.0, "<5"), (5.0, 10.0, "5-10"), (10.0, 20.0, "10-20"), (20.0, 1000.0, "20+")]:
        sub = steady[(steady["win_odds"] >= lo) & (steady["win_odds"] < hi)]
        n, hits, roi = _place_stats(sub)
        if n < 20:
            continue
        print(f"  単勝{label:<6}  n={n:,}  hits={hits}  hit_rate={hits/n*100 if n else 0:.1f}%  複勝ROI={roi:.3f}")

    # ── 4. 比較対象: 複勝モデル上位2位以内 全体(単勝モデルとの一致不問) ──
    print(f"\n{'='*70}\n  比較: 複勝モデル上位2位以内(単勝モデル不問) の複勝成績\n{'='*70}")
    all_top2 = df[df["idx_rank_wf"] <= 2]
    n, hits, roi = _place_stats(all_top2)
    print(f"  全体      n={n:,}  hits={hits}  hit_rate={hits/n*100 if n else 0:.1f}%  複勝ROI={roi:.3f}")


if __name__ == "__main__":
    main()
