"""win_probability をレース内で正規化すべきかを較正指標で判定する。

現状: is_win ヘッドの生確率をそのまま保存（レース内合計は 0.27〜2.29）。
1レースの勝ち馬はちょうど1頭なので、合計は 1.0 であるべき。
正規化が較正（ECE / log-loss / Brier）を改善するかを測る。
"""
from __future__ import annotations
import os, sys
from pathlib import Path
_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_root))
from dotenv import load_dotenv
load_dotenv(_root.parent / ".env")
import numpy as np, pandas as pd, psycopg2  # noqa: E402

conn = psycopg2.connect(host=os.environ['DB_HOST'], port=os.environ['DB_PORT'],
                        dbname=os.environ['DB_NAME'], user=os.environ['DB_USER'],
                        password=os.environ['DB_PASSWORD'])
q = """
select ci.race_id, ci.horse_id, r.course_name, ci.win_probability wp,
       (rr.finish_position = 1)::int won, rr.win_odds
from chihou.calculated_indices ci
join chihou.races r on r.id = ci.race_id
join chihou.race_results rr on rr.race_id = ci.race_id and rr.horse_id = ci.horse_id
where ci.version = 14 and r.course != '83'
  and r.date between '20260101' and '20260830'
  and ci.win_probability is not null and rr.finish_position is not null
"""
df = pd.read_sql(q, conn); conn.close()
df["wp"] = df["wp"].astype(float)
df["wp_norm"] = df["wp"] / df.groupby("race_id")["wp"].transform("sum")

def ece(p, y, bins=20):
    idx = np.clip((p * bins).astype(int), 0, bins - 1)
    tot = 0.0
    for b in range(bins):
        m = idx == b
        if m.sum():
            tot += m.sum() / len(p) * abs(p[m].mean() - y[m].mean())
    return tot

y = df["won"].to_numpy(float)
print(f"母集団: {df['race_id'].nunique():,}R / {len(df):,}行  実勝率={y.mean():.4f}")
print(f"\n{'':14s} {'平均予測':>9s} {'ECE':>8s} {'logloss':>9s} {'Brier':>8s}")
for name, col in [("現行(生確率)", "wp"), ("レース内正規化", "wp_norm")]:
    p = np.clip(df[col].to_numpy(float), 1e-9, 1 - 1e-9)
    ll = -(y * np.log(p) + (1 - y) * np.log(1 - p)).mean()
    print(f"{name:14s} {p.mean():9.4f} {ece(p, y):8.4f} {ll:9.4f} {((p-y)**2).mean():8.4f}")

# 1位馬の的中率は正規化で変わらない（単調変換）ことの確認
for col in ["wp", "wp_norm"]:
    top = df.loc[df.groupby("race_id")[col].idxmax()]
    print(f"  {col:8s} 1位馬勝率 = {top['won'].mean():.4f}")

# EV ゲートへの影響
df["ev"] = df["wp"] * df["win_odds"].astype(float)
df["ev_norm"] = df["wp_norm"] * df["win_odds"].astype(float)
print(f"\nEV∈[1.2,5.0] に入る馬数: 現行 {((df.ev>=1.2)&(df.ev<=5.0)).sum():,} → 正規化 {((df.ev_norm>=1.2)&(df.ev_norm<=5.0)).sum():,}")
for name, col in [("現行", "ev"), ("正規化", "ev_norm")]:
    m = (df[col] >= 1.2) & (df[col] <= 5.0)
    roi = (df.loc[m, "won"] * df.loc[m, "win_odds"].astype(float)).sum() / m.sum()
    print(f"  {name:6s} 該当{m.sum():>6,}頭  的中率{df.loc[m,'won'].mean():.4f}  単勝ROI={roi:.3f}")

# レース内合計の分布
s = df.groupby("race_id")["wp"].sum()
print(f"\nレース内合計: 平均{s.mean():.3f} 中央{s.median():.3f} 5%点{s.quantile(.05):.3f} 95%点{s.quantile(.95):.3f}")
