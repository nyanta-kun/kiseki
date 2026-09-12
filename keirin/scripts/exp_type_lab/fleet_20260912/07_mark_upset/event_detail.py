#!/usr/bin/env python3
"""A3 の細分: y=1 のときの ◎/○ の着順パターン・配当・1着車の pw 順位。"""
import pickle
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent
T = pickle.load((HERE / "event_table.pkl").open("rb"))

def pos(fin, c):
    return {fin[0]: "1", fin[1]: "2", fin[2]: "3"}.get(c, "外")

T["pat"] = [f"◎{pos(f,h)}/○{pos(f,t)}" for f, h, t in zip(T.fin, T.hon, T.tai)]
for win in ("explore", "confirm"):
    S = T[(T.win == win) & T.y]
    print(f"\n===== {win}: y=1 n={len(S):,} (全体の {len(S)/(T.win==win).sum()*100:.1f}%) =====")
    g = S.groupby("pat").agg(n=("y", "size"), odds_med=("tf_odds", "median"), p100=("tf_odds", lambda o: (o >= 100).mean() * 100),
                             p300=("tf_odds", lambda o: (o >= 300).mean() * 100),
                             w_pw2_4=("pw_rank_w", lambda r: ((r >= 2) & (r <= 4)).mean() * 100),
                             w_pw2_3=("pw_rank_w", lambda r: ((r >= 2) & (r <= 3)).mean() * 100),
                             w_pw_med=("pw_w", "median"))
    g["share_y%"] = g.n / len(S) * 100
    g["share_all%"] = g.n / (T.win == win).sum() * 100
    print(g.sort_values("n", ascending=False).round(1).to_string())
    # 1着車の pw 順位 × 2-3着に◎○が居るかのカバレッジ（フォーメーション「1着=pw2〜k位・2着=◎○・3着=流し」の到達点）
    Y = S[S.mk23]
    for k in (2, 3, 4, 5):
        cov = ((Y.pw_rank_w >= 2) & (Y.pw_rank_w <= k)).mean()
        print(f"  y∧mk23 のうち 1着車が pw 2〜{k}位: {cov*100:.1f}%  → 全レース比 {cov*len(Y)/(T.win==win).sum()*100:.1f}%")
    # ◎○が2着(1-2着の順序違い)である割合
    print(f"  y∧mk23 のうち 2着が◎ {(Y.fin.map(lambda f: f[1]) == Y.hon).mean()*100:.1f}%  2着が○ {(Y.fin.map(lambda f: f[1]) == Y.tai).mean()*100:.1f}%  2着が◎○以外 {(~Y.fin.map(lambda f: f[1]).isin([]) & (Y.fin.map(lambda f: f[1]) != Y.hon) & (Y.fin.map(lambda f: f[1]) != Y.tai)).mean()*100:.1f}%")
