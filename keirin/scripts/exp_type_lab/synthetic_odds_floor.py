#!/usr/bin/env python3
"""合成オッズ（=平均想定払戻÷10,000）の下限・上限を動かすと何が起きるか（2026-08-29）。

## 発端

ユーザー提案「買おうとしているレースの**合成オッズ3未満をカット**したらどうなるか」。

🔴 **現行の入稿ゲート `MIN_MEAN_PAYOUT = 20,000円` が既に「合成2.0倍未満」を切っている**
   ので、提案はその線を 2.0 → 3.0 へ上げることと同値。

## 結論（`docs/type_lab/time_and_race_type_2026_08_29.md` 追補）

- **下限を上げるのは逆効果**。表示的中は合成オッズと構造的に逆相関（2.0〜2.5倍で40.2%
  ↔ 6倍超で7.6%）で、**最も当たる帯から順に捨てる**ことになる。無作為対照に 0/20 で負ける
- 悪いのは低い側でなく **6倍超**（ROI 63.8%・実際/予測の払戻比 0.64）
- 🔴 ただし 6倍超の **95.5% が F_pay** ＝ オッズの話ではなく**商品設計の話**

## 測り方

母集団は **本番の全ゲートを再現**する（軸信頼ゲート＝看板素通し／平均払戻2万／1点2.0倍）。
再現しないと「本番が売らないレース」が混ざり、切った効果が水増しされる。
件数を減らす案なので**無作為対照20本を必ず置く**
（[[keirin_type_lab_race_filter_rejected_2026_08_27]] で踏んだ型）。

    PYTHONPATH=. .venv/bin/python scripts/exp_type_lab/synthetic_odds_floor.py
"""
from __future__ import annotations

import importlib.util
import json
from collections import Counter
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from src.database import get_connection
from src.marquee import is_fill_target
from src.type_lab import SELL_PLANS
from src.stake_allocation import MIN_MEAN_PAYOUT, MIN_POINT_ODDS
spec=importlib.util.spec_from_file_location("gate", REPO.parent/"backend/src/services/keirin_type_lab_gate.py")
GATE=importlib.util.module_from_spec(spec); spec.loader.exec_module(GATE)
mspec=importlib.util.spec_from_file_location("mq", REPO.parent/"backend/src/services/keirin_marquee.py")
MQ=importlib.util.module_from_spec(mspec); mspec.loader.exec_module(MQ)
with get_connection() as c:
    rows=[dict(r) for r in c.execute(
      "SELECT t.race_date,t.plan_key,t.axis_sum,t.n_entries,t.race_type,t.budget,t.payout,"
      "       t.pred_mean_payout,t.legs,t.bet_type,r.cup_grade FROM type_lab_picks t "
      "JOIN wt_races r ON r.race_key=t.race_key WHERE t.mode='paper' "
      "AND t.race_date BETWEEN '2025-01-01' AND '2026-08-26' AND t.settled_at IS NOT NULL AND t.budget>0")]
pool=[]
for d in rows:
    if d["plan_key"] not in SELL_PLANS or d.get("pred_mean_payout") is None: continue
    d["marquee"]=bool(MQ.is_marquee_race(d.get("race_type")))
    if not is_fill_target(d.get("race_type"), d.get("cup_grade")):
        if not GATE.passes_axis_gate(str(d["plan_key"]),
            float(d["axis_sum"]) if d["axis_sum"] is not None else None,
            int(d["n_entries"]) if d["n_entries"] is not None else None): continue
    d["mean_pay"]=float(d["pred_mean_payout"])
    if d["mean_pay"]<=MIN_MEAN_PAYOUT: continue
    lg=d["legs"] if isinstance(d["legs"],list) else json.loads(d["legs"] or "[]")
    od=[float(x.get("pred_odds") or 0) for x in lg]; od=[o for o in od if o>0]
    if od and min(od)<MIN_POINT_ODDS: continue
    d["race_date"]=str(d["race_date"]); pool.append(d)
def tally(rs):
    n=len(rs); bet=sum(int(x["budget"]) for x in rs); pay=sum(int(x["payout"] or 0) for x in rs)
    hits=sum(1 for x in rs if int(x["payout"] or 0)>=int(x["budget"]))
    pays=sorted(int(x["payout"]) for x in rs if int(x["payout"] or 0)>=int(x["budget"]))
    return n,(hits/n if n else 0),(pay/bet if bet else 0),(pays[len(pays)//2] if pays else 0),sum(1 for p in pays if p>=100000)
WIN={"探索 2025":("2025-01-01","2025-12-31"),"確認 2026":("2026-01-01","2026-08-26")}

print("=== 6倍超の帯は何でできているか（プラン効果の言い換えでないか） ===")
hi6=[d for d in pool if d["mean_pay"]>60000]
print("  プラン構成:", dict(Counter(d["plan_key"] for d in hi6).most_common()))
print("  券種構成:", dict(Counter(str(d["bet_type"]) for d in hi6).most_common()))
print("\n  プランごとに『6倍超 vs 6倍以下』を割る（両窓・件数100以上）")
print(f"  {'plan':<8}{'帯':<10}{'探索2025':>22}{'確認2026':>22}")
for plan in sorted(set(d["plan_key"] for d in pool)):
    for lab,f in (("<=6倍",lambda d:d["mean_pay"]<=60000),(">6倍",lambda d:d["mean_pay"]>60000)):
        line=f"  {plan:<8}{lab:<10}"
        ok=False
        for w,(lo,hi) in WIN.items():
            g=[d for d in pool if d["plan_key"]==plan and f(d) and lo<=d["race_date"]<=hi]
            if len(g)>=100:
                n,h,r,_,_=tally(g); line+=f"{n:>7,}件 {h:>6.1%} {r:>7.1%}"; ok=True
            else: line+=f"{'—':>22}"
        if ok: print(line)

def scen(name, keep):
    print(f"\n--- {name} ---")
    for w,(lo,hi) in WIN.items():
        sub=[d for d in pool if lo<=d["race_date"]<=hi]; days=len({d['race_date'] for d in sub})
        n0,h0,r0,m0,b0=tally(sub); k=[d for d in sub if keep(d)]; n1,h1,r1,m1,b1=tally(k); nd=n0-n1
        if nd<=0: continue
        ctl=[]
        for s in range(20):
            rng=random.Random(s); idx=set(rng.sample(range(n0),nd))
            ctl.append(tally([d for i,d in enumerate(sub) if i not in idx]))
        wh=sum(1 for x in ctl if h1>x[1]); wr=sum(1 for x in ctl if r1>x[2])
        mq=sum(1 for d in sub if not keep(d) and d["marquee"])
        print(f"  [{w}] {n0/days:5.2f}→{n1/days:5.2f}件/日  表示的中 {h0:.2%}→{h1:.2%}  ROI {r0:.1%}→{r1:.1%}"
              f"  的中中央 {m0:,}→{m1:,}円  10万+ {b0/days:.3f}→{b1/days:.3f}件/日")
        print(f"        対照20本に勝ち 的中 {wh}/20・ROI {wr}/20   落とす看板 {mq}件")
scen("上限: 予測合成 6倍超を売らない", lambda d: d["mean_pay"]<=60000)
scen("上限: 予測合成 8倍超を売らない", lambda d: d["mean_pay"]<=80000)
scen("ユーザー案: 予測合成 3倍未満を売らない（再掲）", lambda d: d["mean_pay"]>30000)

scen("G: F_pay は看板レースだけ売る（非看板の F_pay を落とす）",
     lambda d: d["plan_key"]!="F_pay" or d["marquee"])
scen("H: 予測合成6倍超は看板だけ売る",
     lambda d: d["mean_pay"]<=60000 or d["marquee"])
scen("I: F_pay の中で予測合成6倍超だけ落とす（看板も含む・比較用）",
     lambda d: not (d["plan_key"]=="F_pay" and d["mean_pay"]>60000))

print("\n\n=== F_pay を看板/非看板で割る ===")
print(f"  {'':<12}{'探索2025':>26}{'確認2026':>26}")
for lab,f in (("看板",lambda d:d["marquee"]),("看板でない",lambda d:not d["marquee"])):
    line=f"  {lab:<12}"
    for w,(lo,hi) in WIN.items():
        g=[d for d in pool if d["plan_key"]=="F_pay" and f(d) and lo<=d["race_date"]<=hi]
        n,h,r,med,big=tally(g)
        line+=f"{n:>7,}件 {h:>6.1%} {r:>7.1%} 中央{med:>7,}"
    print(line)
print("\n  10万+ の払戻はどのプランから出ているか（確認窓）")
sub=[d for d in pool if "2026-01-01"<=d["race_date"]<="2026-08-26"]
big=Counter(d["plan_key"] for d in sub if int(d["payout"] or 0)>=100000)
print("   ", dict(big.most_common()))
bigmq=Counter(("看板" if d["marquee"] else "看板でない") for d in sub if int(d["payout"] or 0)>=100000)
print("   ", dict(bigmq.most_common()))
