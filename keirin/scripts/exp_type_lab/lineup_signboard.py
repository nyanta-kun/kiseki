#!/usr/bin/env python3
"""型ラボが今どれだけ看板（10万+ / 30万+）を出しているか（2026-08-31）。

本番の2ゲート（軸信頼＋入稿）を通した「実際に売っている分」だけで、
プラン別に 件/日・表示的中・ROI・10万+/30万+ を出す。
看板案の採否は**この現状表と比べて**決めること。
"""
import json, sys, importlib.util
from collections import defaultdict
from pathlib import Path
from statistics import median
REPO = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(REPO))
from src.database import get_connection
from src.marquee import is_fill_target
from src.stake_allocation import MIN_MEAN_PAYOUT, MIN_POINT_ODDS
from src.type_lab import SELL_PLANS
_s = importlib.util.spec_from_file_location("g", REPO.parent/"backend/src/services/keirin_type_lab_gate.py")
G = importlib.util.module_from_spec(_s); _s.loader.exec_module(G)

with get_connection() as c:
    rows=[dict(r) for r in c.execute(
      "SELECT race_date, race_type, axis_sum, plan_key, n_entries, legs, "
      "       pred_mean_payout, payout FROM type_lab_picks "
      "WHERE mode IN ('paper','paper9') AND settled_at IS NOT NULL AND budget > 0")]
agg=defaultdict(lambda: defaultdict(lambda: [0,0.0,0.0,0,0,set(),[]]))
for d in rows:
    if d["plan_key"] not in SELL_PLANS: continue
    legs=d["legs"] if isinstance(d["legs"],list) else json.loads(d["legs"] or "[]")
    if not legs: continue
    if not (is_fill_target(d.get("race_type"),None) or G.passes_axis_gate(
            d["plan_key"], float(d["axis_sum"]) if d["axis_sum"] is not None else None,
            int(d["n_entries"]) if d["n_entries"] else None)): continue
    mp=d["pred_mean_payout"]
    if mp is not None and float(mp)<=MIN_MEAN_PAYOUT: continue
    po=[float(l.get("pred_odds") or 0) for l in legs]; po=[x for x in po if x>0]
    if po and min(po)<MIN_POINT_ODDS: continue
    y="探索 2025" if str(d["race_date"])<="2025-12-31" else "確認 2026"
    inv=sum(int(l["stake"]) for l in legs); pay=int(d["payout"] or 0)
    for key in ("全体", d["plan_key"]):
        a=agg[y][key]
        a[0]+=1; a[1]+=inv; a[2]+=pay
        a[3]+= int(pay>=100_000); a[4]+= int(pay>=300_000); a[5].add(str(d["race_date"]))
        if pay>inv: a[6].append(1)
for y in ("探索 2025","確認 2026"):
    print(f"\n=== {y} 型ラボが実際に売っている分 ===")
    print(f"  {'商品':<10}{'件/日':>7}{'表示的中':>9}{'ROI':>7}{'10万+本':>9}{'10万+/日':>10}{'30万+本':>9}{'30万+/日':>10}")
    for k in ["全体"]+sorted(x for x in agg[y] if x!="全体"):
        n,inv,pay,b,h,days,sh=agg[y][k]
        nd=len(agg[y]["全体"][5])
        print(f"  {k:<10}{n/nd:>7.2f}{len(sh)/n*100:>8.2f}%{pay/inv*100:>7.1f}{b:>9}{b/nd:>10.3f}{h:>9}{h/nd:>10.3f}")
