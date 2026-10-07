#!/usr/bin/env python3
"""H17 参考: 2026-10-05 防府 5R・6R の f（本番 odds_tf の三連単予測盤面の最小値）と ② の振り分け。DB 読み取りのみ。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.odds_prediction_tf import predicted_trifecta_board
Q1, Q2 = float(sys.argv[1]), float(sys.argv[2])
for k in ("20261005_63_01", "20261005_63_05", "20261005_63_06"):
    b = predicted_trifecta_board(k)
    f = min(b.values()); top = sorted(b.items(), key=lambda kv: kv[1])[:3]
    t = 1 if f <= Q1 else (2 if f <= Q2 else 3)
    print(f"{k} f={f:.3f} 三分位=T{t} ({'F_hit' if t==1 else 'F_sign'}側) 最小3目={[(c, round(o,1)) for c,o in top]} 点数={len(b)}")
