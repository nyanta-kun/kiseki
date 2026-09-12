#!/usr/bin/env python3
"""Q1-2/Q1-3: 表示的中↔10万+/日 の交換レート、および検出力（decidability）。

数字の出所: docs/type_lab/daily_portfolio_2026_09_10.md §5（役割ごとの実測・確認窓）。
ここでは再計算のみ（新しい実測はしない。既存の確定値を組み合わせる）。
"""
import math

# 確認窓・役割ごと（件/日, 表示的中%, 10万+/日）— daily_portfolio_2026_09_10.md §5
ROLES = {
    "当てにいく(_hit/_trio)": (16.66, 30.46, 0.023),
    "配当狙い(A_ana/F_pay)": (1.09, 4.26, 0.032),
    "看板(F_sign)": (3.50, 4.76, 0.120),
    "高額枠({B,C,D}_sign/_big)": (4.44, 3.55, 0.148),
}
N_total = sum(v[0] for v in ROLES.values())
shown_total = sum(v[0] * v[1] for v in ROLES.values()) / N_total
hp_total = sum(v[2] for v in ROLES.values())
print(f"N_total={N_total:.2f}件/日  検算 表示的中={shown_total:.2f}%（doc記載 21.20% と一致するはず）"
      f"  10万+/日={hp_total:.3f}")

hit_shown = ROLES["当てにいく(_hit/_trio)"][1]
hit_hp_rate = ROLES["当てにいく(_hit/_trio)"][2] / ROLES["当てにいく(_hit/_trio)"][0]

print("\n=== 交換レート（当てにいく1件 → 一撃系1件 に置き換えたときの増減） ===")
slopes = []
for name, (n, shown, hp) in ROLES.items():
    if name.startswith("当てにいく"):
        continue
    hp_rate = hp / n
    d_shown_pt = (shown - hit_shown) / N_total          # 全体表示的中への影響(pt)
    d_hp = hp_rate - hit_hp_rate                          # 全体10万+/日への影響
    slope = d_hp / abs(d_shown_pt)
    slopes.append(slope)
    print(f"  {name:28s}  Δ表示的中 {d_shown_pt:+.3f}pt/件  Δ10万+/日 {d_hp:+.4f}/件  "
          f"→ 傾き {slope:.4f}(10万+/日 の増加 ÷ 失うpt)")
avg_slope = sum(slopes) / len(slopes)
print(f"\n  平均の傾き ≈ {avg_slope:.4f}（=1pt の表示的中を諦めると 10万+/日 が約{avg_slope:.3f}増える）")
print(f"  月換算: 1pt 諦めるごとに 10万+ が月に約 {avg_slope*30.4:.2f} 件増える"
      f"（≈{30.4/(avg_slope*30.4):.0f}日に1件多く出る）")

print("\n=== 検出力（80%検出力・両側α=0.05・2群比較） ===")
za, zb = 1.96, 0.84
p = 0.24
print("-- 表示的中（二項検定・n=件/日） --")
for n_per_day in (21, 30, 50, 80):
    row = []
    for d_pt in (1, 2, 3, 5, 10):
        d = d_pt / 100
        n_group = 2 * (za + zb) ** 2 * p * (1 - p) / d ** 2
        days = n_group / n_per_day
        row.append(f"Δ{d_pt}pt:{days:.0f}日")
    print(f"  {n_per_day}件/日: " + "  ".join(row))

print("\n-- 10万+/日（ポアソン近似） --")
for lam in (0.023, 0.120, 0.324):
    row = []
    for mult in (1.1, 1.5, 2.0, 3.0):
        lam2 = lam * mult
        d = lam2 - lam
        lam_avg = (lam + lam2) / 2
        days = 2 * (za + zb) ** 2 * lam_avg / d ** 2
        row.append(f"x{mult}:{days/365:.1f}年")
    print(f"  基準λ={lam}/日: " + "  ".join(row))
