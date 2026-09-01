#!/usr/bin/env python3
"""「荒れる」の選別は「軸が飛ぶ」の選別でしかない、の確認（2026-08-31）。

pw_ent（1着率のエントロピー）／axis_sum で上位から層を切り、
30倍+・100倍+・軸崩壊・軸2車そろい・到達可能な波乱T の割合を並べる。
**T/(30倍+) が選別しても上がらない**ことが結論。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/type_a_mech.py
"""
import importlib.util, math, sys
from collections import defaultdict
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(REPO))
from src.database import get_connection
from src.marquee import is_fill_target
_s = importlib.util.spec_from_file_location("g", REPO.parent/"backend/src/services/keirin_type_lab_gate.py")
G = importlib.util.module_from_spec(_s); _s.loader.exec_module(G)

with get_connection() as c:
    rows=[dict(r) for r in c.execute(
      "SELECT race_key, race_date, race_type, axis_sum, p3_order, win_combo, win_tf_odds "
      "FROM type_lab_picks WHERE mode='paper' AND plan_key='A_hit' AND settled_at IS NOT NULL "
      "AND n_entries=7 AND win_tf_odds IS NOT NULL AND win_combo IS NOT NULL")]
    rows=[d for d in rows if is_fill_target(d.get("race_type"),None) or
          G.passes_axis_gate("A_hit", float(d["axis_sum"]) if d["axis_sum"] is not None else None,7)]
    keys=sorted({d["race_key"] for d in rows}); ent=defaultdict(dict)
    for i in range(0,len(keys),400):
        ch=keys[i:i+400]; ph=",".join("?"*len(ch))
        for r in c.execute(f"SELECT race_key,frame_no,pred_win_pct FROM wt_entries WHERE race_key IN ({ph})",tuple(ch)):
            d=dict(r)
            if d["pred_win_pct"] is not None: ent[d["race_key"]][int(d["frame_no"])]=float(d["pred_win_pct"])
data=[]
for d in rows:
    e=ent.get(d["race_key"],{})
    if len(e)!=7: continue
    s=sum(e.values()) or 1.0
    o=[int(x) for x in str(d["p3_order"]).replace(",","-").split("-") if x]
    f=[int(x) for x in str(d["win_combo"]).split("-")]
    data.append(dict(date=str(d["race_date"]), odds=float(d["win_tf_odds"]),
        pw_ent=-sum((v/s)*math.log(v/s+1e-12) for v in e.values()),
        axis_sum=float(d["axis_sum"] or 0),
        collapse=int(o[0] not in f), both=int(o[0] in f and o[1] in f),
        u30=int(float(d["win_tf_odds"])>=30), u100=int(float(d["win_tf_odds"])>=100),
        T=int(o[0] in f and o[1] in f and float(d["win_tf_odds"])>=30)))
for w,(lo,hi) in {"探索 2025":("2025-01-01","2025-12-31"),"確認 2026":("2026-01-01","2026-08-26")}.items():
    rs=[d for d in data if lo<=d["date"]<=hi]
    print(f"\n=== {w}  {len(rs):,}R ===")
    for lab,key,rev in (("pw_ent（荒れ度の最良単一量）","pw_ent",True),
                        ("axis_sum（低いほど荒れる）","axis_sum",False)):
        srt=sorted(rs,key=lambda d:-d[key] if rev else d[key])
        print(f"  ▼ {lab} 上位から")
        print(f"    {'層':<10}{'R数':>7}{'30倍+':>8}{'100倍+':>9}{'軸崩壊':>8}{'軸2車そろい':>12}{'到達可能な波乱T':>16}")
        n=len(srt)
        for q0,q1,nm in ((0,.1,"上位10%"),(0,.2,"上位20%"),(0,.33,"上位1/3"),
                         (.33,.67,"中1/3"),(.67,1.0,"下1/3"),(0,1.0,"全体")):
            s=srt[int(n*q0):int(n*q1)]
            F=lambda k: sum(d[k] for d in s)/len(s)
            print(f"    {nm:<10}{len(s):>7,}{F('u30'):>8.1%}{F('u100'):>9.1%}"
                  f"{F('collapse'):>8.1%}{F('both'):>12.1%}{F('T'):>16.1%}")
