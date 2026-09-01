#!/usr/bin/env python3
"""型A を**入稿ゲートまで通して**測り直す（2026-08-31）。

🔴 HANDOFF_2026-08-31 の §2/§3 は軸信頼ゲートだけで、
   本番の入稿ゲート（平均想定払戻 > 20,000円 / 全点の予測 >= 2.0倍）を掛けていない。
   型A はゲートで 15% 落ちるが、**落ちる側が一番良い**（素の的中 60.8%）ので、
   ゲート前の数字は「売っている商品」の姿ではない。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/type_a_gated.py
"""
import importlib.util, json, random, sys
from pathlib import Path
from statistics import median
REPO = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(REPO))
from src.database import get_connection
from src.marquee import is_fill_target
from src.stake_allocation import MIN_MEAN_PAYOUT, MIN_POINT_ODDS
_s = importlib.util.spec_from_file_location("g", REPO.parent/"backend/src/services/keirin_type_lab_gate.py")
G = importlib.util.module_from_spec(_s); _s.loader.exec_module(G)

with get_connection() as c:
    rows = [dict(r) for r in c.execute(
        "SELECT race_date, race_type, axis_sum, plan_key, legs, pred_mean_payout, "
        "       payout, win_tf_odds FROM type_lab_picks WHERE mode='paper' "
        "  AND settled_at IS NOT NULL AND n_entries=7 AND plan_key IN ('A_hit','A_pay')")]
def prep(d):
    legs = d["legs"]
    if isinstance(legs, str): legs = json.loads(legs)
    d["legs"] = legs
    d["inv"] = sum(int(x["stake"]) for x in legs)
    d["pay"] = int(d["payout"] or 0)
    d["date"] = str(d["race_date"])
    return d
rows = [prep(d) for d in rows if d["legs"]]
axis_ok = lambda d: (is_fill_target(d.get("race_type"), None) or
    G.passes_axis_gate(d["plan_key"], float(d["axis_sum"]) if d["axis_sum"] is not None else None, 7))
def gate_ok(d):
    mp = d["pred_mean_payout"]
    if mp is not None and float(mp) <= MIN_MEAN_PAYOUT: return False
    o = [float(l.get("pred_odds") or 0) for l in d["legs"]]
    o = [x for x in o if x > 0]
    return not (o and min(o) < MIN_POINT_ODDS)

def boot(rs, n=2000, seed=0):
    rnd = random.Random(seed); m = len(rs)
    inv=[r["inv"] for r in rs]; pay=[r["pay"] for r in rs]
    v=[]
    for _ in range(n):
        a=b=0.0
        for _ in range(m):
            j=rnd.randrange(m); a+=inv[j]; b+=pay[j]
        v.append(b/a*100)
    v.sort(); return v[int(n*.025)], v[int(n*.975)]

def show(lab, rs):
    if not rs: print(f"  {lab:<34} (なし)"); return
    nd=len({r["date"] for r in rs}); inv=sum(r["inv"] for r in rs); pay=sum(r["pay"] for r in rs)
    h=[r for r in rs if r["pay"]>0]; s=[r for r in h if r["pay"]>r["inv"]]
    ps=sorted(r["pay"] for r in h)
    lo,hi=boot(rs)
    print(f"  {lab:<34}{len(rs):>7,}{len(rs)/nd:>7.2f}{len(h)/len(rs)*100:>9.2f}"
          f"{len(s)/len(rs)*100:>9.2f}{median(ps) if ps else 0:>10,.0f}"
          f"{pay/inv*100:>8.1f}[{lo:.0f},{hi:.0f}]"
          f"{sum(1 for x in ps if x>=100_000)/nd:>9.3f}")

for win,(lo_,hi_) in {"探索 2025":("2025-01-01","2025-12-31"),
                      "確認 2026":("2026-01-01","2026-08-26")}.items():
    print(f"\n=== {win} ===")
    print(f"  {'母集団':<34}{'R数':>7}{'件/日':>7}{'素の的中':>9}{'表示的中':>9}{'払戻中央':>10}"
          f"{'ROI(CI95)':>18}{'10万+/日':>9}")
    for pk in ("A_hit","A_pay"):
        base=[d for d in rows if d["plan_key"]==pk and lo_<=d["date"]<=hi_]
        ax=[d for d in base if axis_ok(d)]
        g=[d for d in ax if gate_ok(d)]
        show(f"{pk} ゲート前（軸のみ）", ax)
        show(f"{pk} 入稿ゲート通過（＝売る）", g)
        show(f"{pk} ゲートで落ちた側", [d for d in ax if not gate_ok(d)])
