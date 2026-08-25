"""安い配当 × 信頼度 のクロス。「安いから切る」が正しいかを直接見る。"""
import numpy as np, pandas as pd
D = pd.read_pickle("/tmp/cheap9.pkl")
D["win_"] = D.date < "2026-01-01"
rng = np.random.default_rng(99)

D["conf"] = pd.cut(D.gate, [0, 1.25, 1.40, 3.0], labels=["低(〜1.25)", "中(1.25-1.40)", "高(1.40〜)"])
D["mb"] = pd.cut(D.mean_pay, [0, 15000, 20000, 30000, 1e9],
                 labels=["〜1.5万", "1.5-2万", "2-3万", "3万〜"])

def cell(s):
    if len(s) < 60: return f"{len(s):4d}R      —"
    pay = s.pA.values; inv = 10000*len(s)
    b = [pay[rng.integers(0,len(pay),len(pay))].sum()/inv*100 for _ in range(1200)]
    return (f"{len(s):4d}R 的中{s.hA.mean()*100:5.1f}% ROI{pay.sum()/inv*100:6.1f}%"
            f"[{np.percentile(b,2.5):3.0f},{np.percentile(b,97.5):3.0f}]")

print("=== 三連複5点（現行の買い方）: 想定平均払戻 × 信頼度 ===")
for mb, s in D.groupby("mb", observed=True):
    print(f"■ {mb}")
    for cf, t in s.groupby("conf", observed=True):
        print(f"    {cf:<14} 全 {cell(t)}   | 確認窓 {cell(t[~t.win_])}")
print()
print("=== ゲート(2万円)が切っている母集団の中身 ===")
cut = D[D.mean_pay <= 20000]; keep = D[D.mean_pay > 20000]
for lab, s in (("切られる側(〜2万)", cut), ("残る側(2万〜)", keep)):
    pay = s.pA.values; inv = 10000*len(s)
    print(f"  {lab:<18} {len(s):5d}R ({len(s)/len(D)*100:4.1f}%) "
          f"三連複5点 的中{s.hA.mean()*100:5.1f}% ROI{pay.sum()/inv*100:6.1f}%  "
          f"収支{(pay.sum()-inv)/max(len(s.date.unique()),1):+,.0f}円/日")
    p2 = s[~s.win_]; pay2 = p2.pA.values
    print(f"    └ 確認窓のみ      {len(p2):5d}R 的中{p2.hA.mean()*100:5.1f}% "
          f"ROI{pay2.sum()/(10000*len(p2))*100:6.1f}%")
print()
print("=== 低信頼 × 安い のセルだけ切ったら（ユーザー案）===")
bad = D[(D.mean_pay <= 20000) & (D.gate < 1.25)]
good = D[(D.mean_pay <= 20000) & (D.gate >= 1.25)]
for lab, s in (("低信頼×安い（切る候補）", bad), ("高信頼×安い（残す候補）", good)):
    pay = s.pA.values; inv = 10000*len(s)
    b = [pay[rng.integers(0,len(pay),len(pay))].sum()/inv*100 for _ in range(1500)]
    print(f"  {lab:<24} {len(s):5d}R 的中{s.hA.mean()*100:5.1f}% ROI{pay.sum()/inv*100:6.1f}%"
          f"[{np.percentile(b,2.5):3.0f},{np.percentile(b,97.5):3.0f}]")
    t = s[~s.win_]; tp = t.pA.values
    print(f"    └ 確認窓          {len(t):5d}R 的中{t.hA.mean()*100:5.1f}% ROI{tp.sum()/(10000*len(t))*100:6.1f}%")
print()
print("=== 参考: 信頼度と想定平均払戻の関係 ===")
print(D.groupby("conf", observed=True).mean_pay.describe()[["count","mean","50%"]].round(0))
