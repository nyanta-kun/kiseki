#!/usr/bin/env python3
"""Q1-1: 表示的中の悪い N日窓は「普通」何回来るのか。

母集団: 板（paper・vintage walk-forward・入稿ゲート通過後）/tmp/miss_anatomy_rows.pkl。
探索窓 2024-07〜2025-12 + 確認窓 2026-01〜08 を日付順に連結（約765日）。
当てにいく商品（CORE_PLANS、この台には F_line が無いので7プランのみ）だけを見る。

出す物:
  1. 日次「表示的中」の実測系列（正規化: 全日 同じ商品ミックスではないので、まず全体の
     基準線（全期間平均）を出し、その上で N日移動窓の分布を出す）
  2. N∈{1,3,5,7,14} 日窓ぶんの「基準線から Xpt 以上低い」頻度（月あたり回数・年あたり回数）
  3. 実際の直近の低下（recent_drop_2026_09_11.md の Δ）がこの分布のどこに位置するか
"""
from __future__ import annotations

import pickle
from collections import defaultdict

import numpy as np

CORE_PLANS = {"A_hit", "A_trio", "B_hit", "C_hit", "D_hit", "E_hit", "F_hit"}

rows = pickle.loads(open("/tmp/miss_anatomy_rows.pkl", "rb").read())
rows = [r for r in rows if r["plan"] in CORE_PLANS]
print(f"当てにいく商品の行数: {len(rows):,}")

by_day: dict[str, list] = defaultdict(list)
for r in rows:
    by_day[r["date"]].append(r)

days = sorted(by_day)
print(f"日数: {len(days)}  {days[0]} 〜 {days[-1]}")

n_arr = np.array([len(by_day[d]) for d in days])
shown_arr = np.array([sum(1 for r in by_day[d] if r["shown"]) for d in days])
rate_arr = shown_arr / n_arr

baseline = shown_arr.sum() / n_arr.sum()
print(f"\n全期間の表示的中（基準線） = {baseline*100:.2f}%  (n={n_arr.sum():,})")
print(f"1日あたり件数: 平均 {n_arr.mean():.1f}  最小 {n_arr.min()}  最大 {n_arr.max()}")

# ── N日移動窓（実測の日付順そのまま。真の時系列変動を見る） ──
for N in (1, 3, 5, 7, 14):
    win_rate = []
    win_n = []
    for i in range(len(days) - N + 1):
        n = n_arr[i:i + N].sum()
        s = shown_arr[i:i + N].sum()
        win_n.append(n)
        win_rate.append(s / n if n else np.nan)
    win_rate = np.array(win_rate)
    win_n = np.array(win_n)
    valid = ~np.isnan(win_rate)
    wr = win_rate[valid]
    print(f"\n=== N={N}日窓（{len(wr)}窓・平均件数/窓 {win_n[valid].mean():.1f}） ===")
    print(f"  平均 {wr.mean()*100:.2f}%  SD {wr.std()*100:.2f}pt  "
          f"p10 {np.percentile(wr,10)*100:.2f}  p5 {np.percentile(wr,5)*100:.2f}  "
          f"p1 {np.percentile(wr,1)*100:.2f}  min {wr.min()*100:.2f}")
    for drop_pt in (3, 5, 7, 10, 15):
        thresh = baseline - drop_pt / 100.0
        frac = (wr <= thresh).mean()
        if frac > 0:
            per_month = frac * 30.4 / N if N > 0 else float("nan")
            per_year = frac * 365 / N
            print(f"  基準線−{drop_pt}pt 以下: 全窓の {frac*100:5.1f}%  "
                  f"→ 独立な{N}日窓が月に来る回数の期待値 ≈ {per_month:5.2f} 回/月"
                  f"（年 {per_year:4.1f} 回）")
        else:
            print(f"  基準線−{drop_pt}pt 以下: 0件（{len(wr)}窓中）")

# ── 直近の実測がどこに位置するか ──
print("\n=== 直近の実測（recent_drop_2026_09_11.md）との対応 ===")
print("  当てにいく商品・実入稿の日次表示的中（同ドキュメント §追補）:")
print("    基準 08-29〜09-08 平均 29.19%（推定）")
print("    09-09 30.19% / 09-10 18.87% / 09-11 32.14%")
print("  → 単日 18.87% は基準からΔ-10.3pt。上のN=1日窓の分布で同水準の頻度を見る。")
d = 1
win_rate = []
for i in range(len(days)):
    win_rate.append(rate_arr[i])
win_rate = np.array(win_rate)
thresh = baseline - 0.103
frac = (win_rate <= thresh).mean()
print(f"  板(paper)の1日窓で 基準−10.3pt 以下: {frac*100:.1f}%（{(win_rate<=thresh).sum()}/{len(win_rate)}日）"
      f" → 月に{frac*30.4:.2f}回")
