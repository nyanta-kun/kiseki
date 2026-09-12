"""§4 追補: 高額枠の本数分布と、10万+ の日次の出方。"""
import sys, pickle; sys.path.insert(0,'scripts/exp_type_lab')
import numpy as np
from collections import defaultdict, Counter
import daily_portfolio as D
from src.type_lab import HIGHPAY_SLOTS_PER_DAY, highpay_plan_for
HP = pickle.load(open("/tmp/daily_portfolio_hp.pkl", "rb"))

for nm, rows in (("探索", D.EX), ("確認", D.CF)):
    sold = D.produce(rows); keep = {id(r) for r in sold}
    nd = len(set(r['date'] for r in sold))
    ok = [r for r in rows if r['gate']]
    dropped = defaultdict(list)
    for r in ok:
        if id(r) not in keep and not r['exempt']:
            dropped[r['date']].append(r)
    per, added = [], []
    for d in sorted(set(r['date'] for r in sold)):
        cand = sorted(dropped.get(d, []), key=lambda r: -r['prio'])
        n = 0
        for r in cand:
            if n >= HIGHPAY_SLOTS_PER_DAY: break
            want = highpay_plan_for(r['type'], 7, n)
            if not want: continue
            avail = HP.get(r['race_key']) or {}
            hp = avail.get(want) or next(iter(avail.values()), None)
            if hp is None: continue
            x = dict(hp); x.update(date=d); added.append(x); n += 1
        per.append(n)
    per = np.array(per)
    c = Counter(per.tolist())
    print(f"\n■ {nm}  高額枠の本数/日: 平均 {per.mean():.2f} 本"
          f"（設計 {HIGHPAY_SLOTS_PER_DAY}）")
    print("   " + " ".join(f"{k}本:{c.get(k,0)}日({c.get(k,0)/nd*100:.0f}%)" for k in range(6)))
    # 候補が足りない日の理由
    short = [d for d, n in zip(sorted(set(r['date'] for r in sold)), per) if n < 5]
    ncand = [len([r for r in dropped.get(d, []) if r['type'] in 'BCD']) for d in short]
    print(f"   5本に届かない {len(short)}日 の型B/C/D 見送り候補: 中央 {np.median(ncand):.0f}件"
          f"（0件の日 {sum(1 for x in ncand if x==0)}日）")
    # 10万+ の日次の出方
    allr = sold + added
    dd = defaultdict(int)
    for r in allr:
        if r['pay'] >= 100_000: dd[r['date']] += 1
    cb = Counter(dd.get(d, 0) for d in sorted(set(r['date'] for r in sold)))
    tot = sum(dd.values())
    print(f"   10万+ 合計 {tot}件 = {tot/nd:.3f}件/日（月 {tot/nd*30:.1f}件）")
    print("   日別: " + " ".join(f"{k}件:{cb.get(k,0)}日({cb.get(k,0)/nd*100:.0f}%)" for k in range(4)))
    print(f"   30万+ {sum(1 for r in allr if r['pay']>=300_000)}件 "
          f"= {sum(1 for r in allr if r['pay']>=300_000)/nd*30:.2f}件/月")
    # 役割別
    roles = {"当てにいく(_hit/_trio)": lambda p: p.endswith(("_hit","_trio","_line")),
             "配当を取りにいく(_ana/_pay)": lambda p: p.endswith(("_ana","_pay")),
             "看板(_sign)": lambda p: p.endswith("_sign"),
             "高額枠(_sign/_big・上限で捨てた側)": lambda p: False}
    print(f"   {'役割':32s} {'件/日':>6s} {'表示的中%':>9s} {'10万+/日':>8s} {'払戻中央':>9s}")
    for lab, f in list(roles.items())[:3]:
        s = [r for r in sold if f(r['plan'])]
        if not s: continue
        h = [r['pay'] for r in s if r['pay'] > 0]
        print(f"   {lab:32s} {len(s)/nd:6.2f} "
              f"{sum(1 for r in s if r['pay']>r['inv'])/len(s)*100:9.2f} "
              f"{sum(1 for r in s if r['pay']>=100_000)/nd:8.3f} "
              f"{np.median(h) if h else 0:9,.0f}")
    h = [r['pay'] for r in added if r['pay'] > 0]
    print(f"   {'高額枠(_sign/_big)':32s} {len(added)/nd:6.2f} "
          f"{sum(1 for r in added if r['pay']>r['inv'])/len(added)*100:9.2f} "
          f"{sum(1 for r in added if r['pay']>=100_000)/nd:8.3f} {np.median(h):9,.0f}")
