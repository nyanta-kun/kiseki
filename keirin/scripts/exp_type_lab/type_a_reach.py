#!/usr/bin/env python3
"""型A の波乱が「軸で届く形」かを確定オッズ帯で分解する（2026-08-31）。

型A の 35% は 30倍以上で決着するが、**帯によって中身が全く違う**。
軸2車（p3 1位・2位）が3着内に残っているかを帯ごとに数え、
「どの帯なら軸商品で取りうるか」を先に確定させる。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/type_a_reach.py
"""
import importlib.util, json, sys
from collections import defaultdict
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(REPO))
from src.database import get_connection
from src.marquee import is_fill_target
_s = importlib.util.spec_from_file_location("g", REPO.parent/"backend/src/services/keirin_type_lab_gate.py")
G = importlib.util.module_from_spec(_s); _s.loader.exec_module(G)

with get_connection() as c:
    rows = [dict(r) for r in c.execute(
        "SELECT race_date, race_type, axis_sum, arare, gap, p3_order, win_combo, "
        "       win_tf_odds FROM type_lab_picks WHERE mode='paper' AND plan_key='A_hit' "
        "  AND settled_at IS NOT NULL AND n_entries=7 AND win_tf_odds IS NOT NULL "
        "  AND win_combo IS NOT NULL")]
rows = [d for d in rows if is_fill_target(d.get("race_type"), None) or
        G.passes_axis_gate("A_hit", float(d["axis_sum"]) if d["axis_sum"] is not None else None, 7)]
for d in rows:
    d["date"] = str(d["race_date"])
    d["o"] = [int(x) for x in str(d["p3_order"]).replace(",", "-").split("-") if x]
    d["f"] = [int(x) for x in str(d["win_combo"]).split("-")]
    d["rk"] = [d["o"].index(c) + 1 for c in d["f"]]     # 着順ごとの p3 順位

W = {"探索 2025": ("2025-01-01","2025-12-31"), "確認 2026": ("2026-01-01","2026-08-26")}
BANDS = [("<10倍",0,10),("10-30倍",10,30),("30-100倍",30,100),("100倍+",100,1e9)]
for w,(lo,hi) in W.items():
    rs = [d for d in rows if lo <= d["date"] <= hi]
    print(f"\n=== {w}  型A {len(rs):,}R ===")
    print(f"  {'帯':<10}{'R数':>7}{'割合':>7}"
          f"{'A_hit形(1=軸1,2=軸2)':>20}{'軸1,軸2が上2着(順不同)':>23}"
          f"{'{軸1,軸2}⊂3着内':>17}{'軸1のみ3着内':>13}{'軸崩壊':>8}")
    for lab,b0,b1 in BANDS:
        s=[d for d in rs if b0<=float(d["win_tf_odds"])<b1]
        if not s: continue
        F=lambda fn: sum(1 for d in s if fn(d))/len(s)
        A=lambda d: d["o"][0]; B=lambda d: d["o"][1]
        print(f"  {lab:<10}{len(s):>7,}{len(s)/len(rs):>7.1%}"
              f"{F(lambda d: d['f'][0]==A(d) and d['f'][1]==B(d)):>20.1%}"
              f"{F(lambda d: {d['f'][0],d['f'][1]}=={A(d),B(d)}):>23.1%}"
              f"{F(lambda d: A(d) in d['f'] and B(d) in d['f']):>17.1%}"
              f"{F(lambda d: A(d) in d['f'] and B(d) not in d['f']):>13.1%}"
              f"{F(lambda d: A(d) not in d['f']):>8.1%}")
    # 3着に来た「p3 4位以下」の順位分布（軸2車がそろったレースだけ）
    print(f"  ── {{軸1,軸2}}⊂3着内 のレースで、残り1車の p3 順位 ──")
    g=defaultdict(int); tot=0
    for d in rs:
        A,B_=d["o"][0],d["o"][1]
        if A in d["f"] and B_ in d["f"]:
            third=[c for c in d["f"] if c not in (A,B_)][0]
            g[d["o"].index(third)+1]+=1; tot+=1
    print("    " + "  ".join(f"{k}位 {v/tot:.1%}" for k,v in sorted(g.items())) + f"   (n={tot:,})")
