#!/usr/bin/env python3
"""台の検算 — 既知の本番アンカーと突き合わせる。

🔴 これを先にやる理由（CLAUDE.md「baseline は実データを1件表示して目視確認する」）。
   件数の絶対値を主張する腕なので、台が本番の何倍/何分の一なのかを先に出す。
"""
from __future__ import annotations
import sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, "/Users/ysuzuki/GitHub/kiseki/keirin")
import lib12 as L

print("=== ① 上限を掛けない場合（low_confidence §4.1 の 33.02件/日 と突き合わせ）===")
for win in ("explore", "confirm"):
    nd = L.ndays(win)
    r = L.simulate(win, hp_slots=0, frac=0.0)
    print(f"  {win}: {len(r)/nd:6.2f}件/日  表示的中 {L.kpi(r,nd)['shown']:.2f}%"
          f"   （既知: 確認 33.02件/日 / 27.70%）")

print("\n=== ② 上限を掛けた場合（gate doc の DAILY_CAP 表『枠外を除く×0.5』25.30/25.64 と突き合わせ）===")
for win in ("explore", "confirm"):
    nd = L.ndays(win)
    r = [x for x in L.simulate(win, hp_slots=0) if x["origin"] != "hp:cap"]
    print(f"  {win}: {len(r)/nd:6.2f}件/日  表示的中 {L.kpi(r,nd)['shown']:.2f}%"
          f"   （既知: 25.30 / 25.64件/日・22.24 / 23.40%）")

print("\n=== ③ 上限落ちの本数を本番の実運用値（daily_cap 13.4R/日・2026-09-01〜05）へ較正 ===")
print("  🔴 台は **並び未公開（後の波へ回す＝分母から外す）** を再現できないので分母が過大＝上限が甘い。")
print("  分母に λ を掛けて上限落ちが 13.4R/日 になる λ を探す。")
for win in ("explore", "confirm"):
    nd = L.ndays(win)
    print(f"  [{win}]")
    for lam in (0.50, 0.42, 0.38, 0.35, 0.32, 0.30, 0.28, 0.25):
        inv = L.inventory(win, frac=lam)
        c = Counter(k for _r, k, _w in inv)
        bcd = sum(1 for r, k, _w in inv if k == "cap" and r["type"] in "BCD")
        print(f"    λ={lam:.2f}  上限落ち {c['cap']/nd:6.2f}件/日"
              f"  うちB/C/D {bcd/nd:5.2f}  （本番: 上限落ち 13.4 / 高額枠 4.40〜4.44）")
